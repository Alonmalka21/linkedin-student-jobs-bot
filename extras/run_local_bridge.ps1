# Local bridge: runs the watcher directly on Alon's PC when GitHub Actions
# is down, and pushes state updates to the repo so the cloud stays deduped.
# Requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID as user env vars, and git
# push access (gh auth). Scheduled via Task Scheduler every 15 minutes.

Set-Location (Split-Path $PSScriptRoot -Parent)

git pull --rebase origin main | Out-Null

$env:PYTHONIOENCODING = "utf-8"
& python watcher.py *> "$PSScriptRoot\local_bridge_last_run.log"

git diff --quiet -- state/seen_jobs.json
if ($LASTEXITCODE -ne 0) {
    git add state/seen_jobs.json
    git commit -m "chore: update seen jobs (local bridge)"
    git pull --rebase origin main
    git push origin main
}
