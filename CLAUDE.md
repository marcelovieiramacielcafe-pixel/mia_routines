# CLAUDE.md

Guidance for AI assistants (Claude Code and others) working in this repository.

## What this project is

`mia_routines` is a small, single-purpose Python job that keeps a **Segundo
Cérebro** ("Second Brain") in sync with an Outlook mailbox. One invocation:

1. Connects to Outlook over IMAP.
2. Fetches mail newer than the last message already indexed (or the last N
   days on the first run).
3. Classifies each message into `contas_a_pagar`, `contabilidade`, or `other`
   using a heuristic scorer.
4. Stores every message in a local SQLite database and logs a run summary.

It is built to run on **Claude Routines** (or cron / manual) on a schedule, so
it is **idempotent**: re-running only fetches and indexes new mail.

The user's primary language is **Portuguese**; classifier keywords and
categories are Portuguese-first. Keep that in mind when editing the classifier.

## Layout

```
mia_routines/
├── main_routine.py            # entry point — orchestrates a single run
├── requirements.txt           # pinned runtime deps (imap-tools, python-dotenv)
├── .env.example               # template for local secrets/config
├── README.md                  # user-facing setup & usage docs
└── mia_routines/              # the package
    ├── __init__.py            # version string
    ├── config.py              # Config dataclass, loaded from environment
    ├── classifier.py          # heuristic categorisation (pure, no I/O)
    ├── imap_client.py         # imap-tools wrapper, yields FetchedEmail
    └── second_brain.py        # SQLite store (SecondBrain, StoredEmail)
```

### Data flow / module responsibilities

`main_routine.run()` is the only orchestrator. It wires the pieces together:

- **`config.Config`** — frozen dataclass built by `Config.from_env()`. Reads
  all settings from environment variables (loading `.env` via `python-dotenv`
  if present). Raises if `OUTLOOK_USER` / `OUTLOOK_APP_PASSWORD` are missing.
  Creates the DB parent directory. **All configuration lives here** — do not
  read `os.environ` elsewhere.
- **`imap_client.OutlookIMAP`** — wraps `imap-tools`. `fetch_since(folder, ...)`
  yields `FetchedEmail` objects: UID > `min_uid`, or a `lookback_days` date
  window when `min_uid == 0` (first run). Pure fetching; no classification or
  persistence. Capped at `max_messages=5000` per folder per run.
- **`classifier.classify(...)`** — pure function, no I/O. Scores the email
  against keyword sets and sender lists for each category, returns a
  `Classification(category, score, matched_terms)`. Sender matches weigh **3×**
  keyword matches. Returns `other` with score 0 when nothing matches.
- **`second_brain.SecondBrain`** — SQLite store, the source of truth for
  "what have we already indexed?". `max_uid_for(folder)` drives incremental
  fetching; `insert_email()` returns `False` on the `UNIQUE (folder, uid)`
  conflict so re-runs are safe.

## Setup & running

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit OUTLOOK_USER and OUTLOOK_APP_PASSWORD
python main_routine.py
```

Exit code is `0` on success, `1` if the run caught an exception (still logged
to `run_log`).

There is **no virtualenv, build step, packaging, or Makefile** — it runs
directly with the stdlib plus the two pinned deps. Python 3.10+ is required
(the code uses `X | None` union syntax and `from __future__ import annotations`).

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `OUTLOOK_USER` | — (required) | Mailbox address |
| `OUTLOOK_APP_PASSWORD` | — (required) | 16-char Outlook App Password (needs 2FA) |
| `OUTLOOK_IMAP_HOST` | `outlook.office365.com` | IMAP host |
| `OUTLOOK_IMAP_PORT` | `993` | IMAP port |
| `SECOND_BRAIN_DB` | `./data/second_brain.db` | SQLite path (parent auto-created) |
| `INITIAL_LOOKBACK_DAYS` | `180` | First-run date window |
| `IMAP_FOLDERS` | `INBOX` | Comma-separated folders to scan |
| `VERBOSE` | `0` | Set `1` to log per-message classifier decisions |

When adding a new setting, add it in three places to keep them in sync:
`config.py` (`Config` field + `from_env`), `.env.example`, and this table.

## Database

SQLite with two tables (schema in `second_brain.py:SCHEMA`, created on init):

- **`emails`** — one row per `(folder, uid)` (enforced UNIQUE). Stores the raw
  message fields, a `body_preview`, `attachments`/`classifier_terms` as JSON
  strings, and the classifier's `category` + `classifier_score`.
- **`run_log`** — one row per invocation: timestamps, per-category counts, and
  any `error` string.

Inspect with:

```bash
sqlite3 data/second_brain.db "SELECT category, COUNT(*) FROM emails GROUP BY category;"
sqlite3 data/second_brain.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 5;"
```

The DB and the `data/` directory are git-ignored. Schema migrations are done
by editing `SCHEMA` with `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT
EXISTS`; there is no migration framework, so existing-table column additions
need explicit `ALTER TABLE` handling if you add them.

## Conventions to follow

- **Style**: standard-library-first, no framework. `from __future__ import
  annotations` at the top of every module. Modern type hints (`str | None`,
  `tuple[str, ...]`). Frozen `@dataclass` for value objects (`Config`,
  `Classification`); plain `@dataclass` for the mutable record carriers
  (`FetchedEmail`, `StoredEmail`).
- **Keyword-only boundaries**: `classify(...)` and `fetch_since(...)` take
  keyword-only args (`*`). Preserve this when extending them.
- **Separation of concerns**: keep `classifier.py` pure (no I/O, no env), keep
  IMAP details in `imap_client.py`, keep all SQL in `second_brain.py`, and keep
  orchestration/printing in `main_routine.py`. Don't leak responsibilities
  across these.
- **Logging** is plain `print()` with `[mia_routines]` / `[folder]` prefixes —
  this is intentional for Routines log capture. No logging framework.
- **Idempotency is a hard requirement.** Any change must preserve "re-running
  only indexes new mail." Don't break the `max_uid_for` → `fetch_since` →
  `UNIQUE(folder, uid)` chain.

## Security — credentials

**Never** hardcode, print, or commit secrets. `OUTLOOK_APP_PASSWORD` is a
production credential.

- `.env` and `.env.*` are git-ignored (except `.env.example`). Keep real
  values out of git, chat, commits, and PRs.
- For Claude Routines, set `OUTLOOK_USER` / `OUTLOOK_APP_PASSWORD` as Routine
  secrets, not in the repo.
- Email bodies stored in the DB may contain sensitive content — the DB file
  stays local and git-ignored.

## Tests & tooling

There are currently **no tests, linters, CI, or formatters** configured. If
you add logic worth testing, the `classifier.classify` function is pure and the
natural first target (no mocking needed). Prefer `pytest` and place tests under
a `tests/` directory if you introduce them, and document the run command here.

## Git / contribution workflow

- Work on the feature branch you were assigned; create it locally if needed.
- Commit with clear, descriptive messages; push with `git push -u origin <branch>`.
- After pushing, open a **draft** pull request if one doesn't already exist.
- Never push directly to `main` without explicit permission.
