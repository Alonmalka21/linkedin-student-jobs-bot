#!/usr/bin/env python3
"""LinkedIn student-jobs watcher.

Polls LinkedIn's public guest jobs API for the searches defined in config.json,
detects postings not seen before, and sends a Telegram alert for each new one.
Designed to run under GitHub Actions on a schedule (or locally on Windows).

No third-party dependencies: Python 3.10+ standard library only.

Env vars:
  TELEGRAM_BOT_TOKEN  - bot token from @BotFather
  TELEGRAM_CHAT_ID    - target chat id
If either is missing the script runs in dry-run mode and only prints.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state" / "seen_jobs.json"

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_POSTING_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
JOB_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
}

MAX_RESULTS_PER_SEARCH = 30    # newest N per search per run (feeds are date-sorted)
RETRY_DELAYS = (5, 15, 30)     # seconds between retries on HTTP errors
PAGE_PAUSE = 1.5               # polite pause between page fetches
DIGEST_THRESHOLD = 12          # above this many new jobs, send digests instead
MAX_SEEN_IDS = 5000            # cap the state file size


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str) -> str:
    last_err = None
    for i in range(len(RETRY_DELAYS) + 1):
        if i:
            time.sleep(RETRY_DELAYS[i - 1])
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            last_err = exc
            log(f"  fetch attempt {i + 1} failed: {exc}")
    raise RuntimeError(f"GET failed after retries: {url} ({last_err})")


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_jobs(page_html: str) -> list[dict]:
    """Parse job cards out of a guest-API HTML fragment."""
    jobs = []
    chunks = re.split(r'data-entity-urn="urn:li:jobPosting:', page_html)
    for chunk in chunks[1:]:
        m_id = re.match(r"(\d+)", chunk)
        if not m_id:
            continue
        job_id = m_id.group(1)

        def field(pattern: str) -> str:
            m = re.search(pattern, chunk, re.S)
            return strip_tags(m.group(1)) if m else ""

        title = field(r'<h3[^>]*base-search-card__title[^>]*>(.*?)</h3>')
        company = field(r'<h4[^>]*base-search-card__subtitle[^>]*>(.*?)</h4>')
        location = field(r'<span[^>]*job-search-card__location[^>]*>(.*?)</span>')
        posted = field(r'<time[^>]*>(.*?)</time>')

        jobs.append(
            {
                "id": job_id,
                "title": title or "(no title)",
                "company": company,
                "location": location,
                "posted": posted,
                "url": JOB_VIEW_URL.format(job_id=job_id),
            }
        )
    return jobs


def _fetch_pass(
    params: dict, sort_by: str, seen_set: set[str], early_stop: bool
) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    start = 0
    while start < MAX_RESULTS_PER_SEARCH:
        query = dict(params)
        query["sortBy"] = sort_by
        query["start"] = str(start)
        url = GUEST_API + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        page = parse_jobs(http_get(url))
        if not page:
            break
        for job in page:
            jobs.setdefault(job["id"], job)
        start += len(page)
        if early_stop and all(job["id"] in seen_set for job in page):
            break
        time.sleep(PAGE_PAUSE)
    return jobs


def fetch_search(params: dict, seen_set: set[str]) -> list[dict]:
    """Fetch one search in two cross-validating passes and union the results.

    The guest feed "flickers": each sort order occasionally drops jobs
    (promoted ones especially), and what one sort misses the other tends to
    serve. Pass 1 sorts by date (newest first), so once a whole page is
    already known everything deeper is older and known too - stop early.
    Pass 2 sorts by relevance at fixed depth, mirroring the default view
    of the search page; it may not stop early, a new job can rank anywhere.
    """
    jobs = _fetch_pass(params, "DD", seen_set, early_stop=True)
    date_count = len(jobs)
    for job_id, job in _fetch_pass(params, "R", seen_set, early_stop=False).items():
        jobs.setdefault(job_id, job)
    log(f"  pass date: {date_count} jobs, pass relevance: +{len(jobs) - date_count} unique")
    return list(jobs.values())


def fetch_job_description(job_id: str) -> str:
    """Fetch a job's full description text via the guest job-posting API."""
    page = http_get(JOB_POSTING_API.format(job_id=job_id))
    m = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
    if not m:
        log(f"  NOTE: no description markup parsed for job {job_id}")
        return ""
    return strip_tags(m.group(1))


def word_in_text(word: str, text: str) -> bool:
    """Whole-word match (Unicode-aware, so Hebrew word boundaries work)."""
    return bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE))


def match_label(job: dict, searches: list[dict]) -> str | None:
    """Decide which search (if any) this candidate genuinely belongs to.

    The guest feed loosens LinkedIn's quoted-keyword semantics and pads
    results with "similar jobs"; the logged-in view matches the quoted
    phrase exactly. Calibration against Alon's logged-in results
    (2026-08-06, 6 EN + 1 HE) showed the quoted search matches the job
    TITLE only, so that is the default. A search can opt into description
    matching with "match_in": ["title", "description"].

    Returns the matching search's label, "" when nothing matches (drop),
    or None when the decision could not be made this cycle (fetch failed).
    """
    for idx, search in enumerate(searches):
        if not search.get("match_word") and idx in job["sources"]:
            return search.get("label", "?")
    checks = [
        (s.get("label", "?"), s["match_word"], s.get("match_in", ["title"]))
        for s in searches
        if s.get("match_word")
    ]
    if not checks:
        return ""
    for label, word, where in checks:
        if "title" in where and word_in_text(word, job["title"]):
            return label
    if any("description" in where for _, _, where in checks):
        try:
            description = fetch_job_description(job["id"])
        except RuntimeError as exc:
            log(f"  description fetch failed for job {job['id']} (retry next cycle): {exc}")
            return None
        finally:
            time.sleep(0.8)
        for label, word, where in checks:
            if "description" in where and word_in_text(word, description):
                return label
    return ""


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_state(seen_ids: list[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = seen_ids[-MAX_SEEN_IDS:]
    STATE_FILE.write_text(
        json.dumps({"seen": trimmed}, ensure_ascii=False, indent=0) + "\n",
        encoding="utf-8",
    )


def telegram_send(token: str, chat_id: str, text: str, button_url: str | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if button_url:
        payload["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "פתיחת המשרה בלינקדאין", "url": button_url}]]}
        )
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body.get('description', body)}")
    time.sleep(0.5)  # stay far below Telegram rate limits


def format_job(job: dict) -> str:
    lines = [
        f"🆕 משרת סטודנט חדשה ({job['search_label']})",
        f"📌 {job['title']}",
    ]
    if job["company"]:
        lines.append(f"🏢 {job['company']}")
    if job["location"]:
        lines.append(f"📍 {job['location']}")
    if job["posted"]:
        lines.append(f"🕒 {job['posted']}")
    return "\n".join(lines)


def format_digest(jobs: list[dict]) -> str:
    lines = []
    for job in jobs:
        lines.append(f"📌 {job['title']} | {job['company']}")
        lines.append(f"   {job['url']}")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    dry_run = not (token and chat_id)
    if dry_run:
        log("[dry-run] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; printing only.")

    config = load_json(CONFIG_FILE, None)
    if not config or not config.get("searches"):
        log("ERROR: config.json missing or has no searches.")
        return 1

    state = load_json(STATE_FILE, {"seen": []})
    seen_ids = list(state.get("seen", []))
    seen_set = set(seen_ids)

    loc_terms = [
        t.lower() for t in config.get("filters", {}).get("location_must_contain", [])
    ]

    # Phase 1: collect unseen candidates from all searches (the guest feed
    # is a superset of the real searches, so candidates from any feed are
    # judged against every search's match_word in phase 2).
    candidates: dict[str, dict] = {}
    fetch_failures = 0
    for idx, search in enumerate(config["searches"]):
        label = search.get("label", "?")
        log(f"Search [{label}]: fetching...")
        try:
            jobs = fetch_search(search["params"], seen_set)
        except RuntimeError as exc:
            log(f"  WARNING: search [{label}] failed this cycle: {exc}")
            fetch_failures += 1
            continue
        fresh = [j for j in jobs if j["id"] not in seen_set]
        log(f"  {len(jobs)} jobs returned, {len(fresh)} unseen candidates.")
        for job in fresh:
            entry = candidates.setdefault(job["id"], {**job, "sources": set()})
            entry["sources"].add(idx)

    # Phase 2: decide per candidate.
    new_jobs: list[dict] = []
    state_dirty = False
    for job in candidates.values():
        if (
            loc_terms
            and job["location"]
            and not any(t in job["location"].lower() for t in loc_terms)
        ):
            # LinkedIn occasionally leaks out-of-region results into the
            # feed; drop them but remember them so they are not re-checked.
            log(f"  [skip location] {job['title']} | {job['location']}")
            seen_set.add(job["id"])
            seen_ids.append(job["id"])
            state_dirty = True
            continue
        label = match_label(job, config["searches"])
        if label is None:
            continue  # undecided this cycle; stays unseen and is retried
        if not label:
            log(f"  [skip keyword] {job['title']} | {job['company']}")
            seen_set.add(job["id"])
            seen_ids.append(job["id"])
            state_dirty = True
            continue
        job["search_label"] = label
        seen_set.add(job["id"])
        new_jobs.append(job)

    # Oldest first, so the newest posting ends up last (bottom of the chat).
    new_jobs.reverse()

    log(f"Total new jobs this cycle: {len(new_jobs)}")
    try:
        if new_jobs and not dry_run:
            if len(new_jobs) > DIGEST_THRESHOLD:
                telegram_send(
                    token, chat_id,
                    f"🗂 נמצאו {len(new_jobs)} משרות חדשות, נשלחות כרשימות מרוכזות:",
                )
                for i in range(0, len(new_jobs), 5):
                    batch = new_jobs[i:i + 5]
                    telegram_send(token, chat_id, format_digest(batch))
                    seen_ids.extend(j["id"] for j in batch)
            else:
                for job in new_jobs:
                    telegram_send(token, chat_id, format_job(job), button_url=job["url"])
                    seen_ids.append(job["id"])
        else:
            for job in new_jobs:
                log("  [new] " + format_job(job).replace("\n", " | ") + " " + job["url"])
                seen_ids.append(job["id"])
    finally:
        # Save whatever was processed so far; unsent jobs stay unseen and
        # will be retried on the next cycle.
        if new_jobs or state_dirty:
            save_state(seen_ids)

    if fetch_failures and not new_jobs:
        # Let the workflow show red if LinkedIn blocked every search.
        return 1 if fetch_failures == len(config["searches"]) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
