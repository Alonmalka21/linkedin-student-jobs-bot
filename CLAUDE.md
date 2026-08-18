# LinkedIn student-jobs watcher

מנטר שני חיפושי משרות סטודנט בלינקדאין (EN + עברית, ישראל, 24 שעות) ושולח התראת טלגרם על כל משרה חדשה. פרטים מלאים ב-README.md.

- Stack: Python 3 stdlib בלבד (`watcher.py`), GitHub Actions cron כל 15 דקות, Telegram Bot API.
- מקור הנתונים: LinkedIn guest jobs API (`jobs-guest/jobs/api/seeMoreJobPostings/search`), ללא התחברות. לשמור על קצב מנומס: השהיות בין עמודים, עומק סריקה מוגבל (MAX_RESULTS_PER_SEARCH). אין עצירה מוקדמת על עמוד מוכר: הפיד דוגם, ומשרה חדשה יכולה להופיע עמוק מאחורי עמוד שכולו מוכר (נצפה 2026-08-17).
- קריטי: הפיד הציבורי מרחיב את החיפוש ומרפד ב"משרות דומות"; רק משתמש מחובר רואה התאמת מרכאות מדויקת. לכן כל מועמדת עוברת סינון מקומי: מיקום ישראל + `match_word` כמילה שלמה בכותרת או בתיאור (`jobs-guest/jobs/api/jobPosting/{id}`). כויל מול התצוגה המחוברת של אלון (6 EN + 1 HE ב-2026-08-06).
- State: `state/seen_jobs.json`, מזהי משרות שכבר דווחו. נכתב בחזרה לריפו על ידי ה-workflow.
- סודות: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ב-GitHub Secrets בלבד, לעולם לא בקוד.
- בדיקה מקומית: להריץ `python watcher.py` בלי משתני סביבה = dry-run שמדפיס בלבד.
- כלל הפלייבוק: לאמת בהרצה אמיתית, לא בהסקה.
