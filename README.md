# mia_routines — Outlook → Second Brain indexer

Routine that keeps the **Segundo Cérebro** in sync with Outlook. Each run pulls
new mail via IMAP, classifies it (`contas_a_pagar` / `contabilidade` / `other`),
and stores it in a SQLite database.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with OUTLOOK_USER and OUTLOOK_APP_PASSWORD
python main_routine.py
```

The first run uses `INITIAL_LOOKBACK_DAYS` (default 180) as a date window.
Subsequent runs are incremental — they fetch only UIDs above the last one
already indexed per folder.

## Outlook App Password

App Passwords are 16-character strings issued at
<https://account.microsoft.com/security>. They require 2FA on the account.
Treat them as production credentials: never paste into chat, never commit
to git. If one leaks, revoke it from the same page.

## What gets classified as what

The classifier is heuristic, scoring each message against keyword sets and
sender domains.

- **contas_a_pagar** — invoices, payment reminders, recurring billing
  (Hetzner, EDP, MEO, Stripe, AWS, etc.).
- **contabilidade** — anything from `at.gov.pt`, `seg-social.pt`, references
  to IVA/IRS/IES/Modelos, payroll processing, accountant communications.
- **other** — fallback bucket; still indexed, but ignored by the routine's
  primary mission.
- **error** — sentinel for a message that couldn't be processed (e.g. a photo
  attachment with a broken filename encoding). It's recorded so the per-folder
  UID watermark advances past it; without this the message would be re-fetched
  and re-fail on every run — an endless error loop. The original failure is
  kept in `body_preview`/`classifier_terms` for inspection.

Sender matches weigh 3× because they're far more reliable than keyword
overlap in noisy newsletter content.

## Database

SQLite at `SECOND_BRAIN_DB` (default `./data/second_brain.db`). Two tables:

- `emails` — one row per `(folder, uid)`, with category and classifier score.
- `run_log` — one row per routine invocation, with per-category counts.

To inspect:

```bash
sqlite3 data/second_brain.db "SELECT category, COUNT(*) FROM emails GROUP BY category;"
sqlite3 data/second_brain.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 5;"
```

## Running on Claude Routines

1. Push this repo to GitHub.
2. In the Claude panel, create a Routine pointing at the repo and `main_routine.py`.
3. Set the secrets (`OUTLOOK_USER`, `OUTLOOK_APP_PASSWORD`) in the Routine's
   environment — never commit them.
4. Pick a cadence (every 30 min is reasonable for a mail indexer).

## Project layout

```
mia_routines/
├── main_routine.py           # entry point
├── requirements.txt
├── .env.example
├── README.md
└── mia_routines/
    ├── __init__.py
    ├── config.py             # env loader
    ├── classifier.py         # heuristic categorisation
    ├── imap_client.py        # imap-tools wrapper
    └── second_brain.py       # SQLite store
```
