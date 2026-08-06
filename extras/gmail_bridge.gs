/**
 * LinkedIn job-alert emails -> Telegram bridge (Google Apps Script).
 *
 * Purpose: LinkedIn serves some promoted/reposted jobs only to logged-in
 * members, so the guest-based watcher cannot see them. LinkedIn's native
 * alert emails DO include them. This script forwards jobs from those emails
 * to the same Telegram bot, skipping anything the watcher already sent
 * (cross-checked against the public state file in the GitHub repo).
 *
 * Setup (one time, ~5 minutes, on the Google account that RECEIVES the
 * LinkedIn emails):
 *   1. Go to https://script.google.com > New project, paste this file.
 *   2. Fill TELEGRAM_BOT_TOKEN below (from @BotFather).
 *   3. Run checkAlerts once from the toolbar and approve the Gmail
 *      permission prompt.
 *   4. Left menu > Triggers > Add trigger: checkAlerts, time-driven,
 *      every 10 minutes.
 */

const CONFIG = {
  TELEGRAM_BOT_TOKEN: "PASTE_TOKEN_FROM_BOTFATHER",
  TELEGRAM_CHAT_ID: "941566037",
  STATE_URL:
    "https://raw.githubusercontent.com/Alonmalka21/linkedin-student-jobs-bot/main/state/seen_jobs.json",
  GMAIL_QUERY: "from:jobalerts-noreply@linkedin.com newer_than:1d",
  MAX_SENT_IDS: 2000,
};

function checkAlerts() {
  const props = PropertiesService.getScriptProperties();
  const sentIds = new Set(JSON.parse(props.getProperty("sent_ids") || "[]"));

  // Jobs the GitHub watcher already alerted on (public state file).
  let botSeen = new Set();
  try {
    const state = JSON.parse(UrlFetchApp.fetch(CONFIG.STATE_URL).getContentText());
    botSeen = new Set(state.seen || []);
  } catch (e) {
    // If GitHub is unreachable, worst case is a duplicate alert.
  }

  const threads = GmailApp.search(CONFIG.GMAIL_QUERY, 0, 20);
  let added = false;
  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      const subject = msg.getSubject();
      const body = msg.getBody();
      // Matches both /jobs/view/<id> and /comm/jobs/view/<id> links.
      const matches = body.match(/jobs\/view\/(\d+)/g) || [];
      const ids = Array.from(new Set(matches.map(function (m) {
        return m.replace(/\D/g, "");
      })));
      ids.forEach(function (id) {
        if (sentIds.has(id) || botSeen.has(id)) return;
        sendTelegram(
          "📧 משרה מהתראת המייל של לינקדאין\n" +
          "📌 " + subject + "\n" +
          "https://www.linkedin.com/jobs/view/" + id + "/"
        );
        sentIds.add(id);
        added = true;
      });
    });
  });

  if (added) {
    const trimmed = Array.from(sentIds).slice(-CONFIG.MAX_SENT_IDS);
    props.setProperty("sent_ids", JSON.stringify(trimmed));
  }
}

function sendTelegram(text) {
  UrlFetchApp.fetch(
    "https://api.telegram.org/bot" + CONFIG.TELEGRAM_BOT_TOKEN + "/sendMessage",
    {
      method: "post",
      payload: {
        chat_id: CONFIG.TELEGRAM_CHAT_ID,
        text: text,
        disable_web_page_preview: "true",
      },
    }
  );
}
