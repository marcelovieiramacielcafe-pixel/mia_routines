"""Entry point for Claude Routines.

Each invocation:
1. Loads config from environment.
2. Connects to Outlook IMAP.
3. Fetches messages newer than the highest UID we've already indexed
   (or the last N days on the first run).
4. Classifies each message (contas_a_pagar / contabilidade / other).
5. Writes to the Second Brain SQLite store.
6. Logs a run summary.

Designed to be idempotent: re-running fetches only new mail. Safe to run
on any cadence (Claude Routines schedule, cron, manual).
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone

from mia_routines.classifier import classify
from mia_routines.config import Config
from mia_routines.imap_client import OutlookIMAP
from mia_routines.second_brain import SecondBrain, StoredEmail


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(text: str | None, *, length: int = 400) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    return cleaned[:length]


def run() -> int:
    config = Config.from_env()
    brain = SecondBrain(config.second_brain_db)
    imap = OutlookIMAP(
        host=config.imap_host,
        port=config.imap_port,
        user=config.outlook_user,
        password=config.outlook_app_password,
    )

    started = _utcnow_iso()
    run_id = brain.start_run(started)
    counts = {"total": 0, "contas_a_pagar": 0, "contabilidade": 0, "other": 0}
    error: str | None = None

    print(f"[mia_routines] run started at {started} — folders={list(config.folders)}")

    try:
        for folder in config.folders:
            min_uid = brain.max_uid_for(folder)
            print(f"[{folder}] fetching from uid>{min_uid} (lookback {config.initial_lookback_days}d if first run)")

            for fetched in imap.fetch_since(
                folder,
                min_uid=min_uid,
                lookback_days=config.initial_lookback_days,
            ):
                classification = classify(
                    sender=fetched.sender,
                    subject=fetched.subject,
                    body=fetched.body_text,
                )

                inserted = brain.insert_email(StoredEmail(
                    folder=fetched.folder,
                    uid=fetched.uid,
                    message_id=fetched.message_id,
                    date_utc=fetched.date_utc,
                    sender=fetched.sender,
                    recipients=fetched.recipients,
                    subject=fetched.subject,
                    body_text=fetched.body_text,
                    body_preview=_preview(fetched.body_text),
                    attachments=fetched.attachments,
                    category=classification.category,
                    classifier_score=classification.score,
                    classifier_terms=classification.matched_terms,
                ))

                if not inserted:
                    continue

                counts["total"] += 1
                counts[classification.category] = counts.get(classification.category, 0) + 1

                if config.verbose:
                    print(
                        f"  [{classification.category}] uid={fetched.uid} "
                        f"score={classification.score} from={fetched.sender} "
                        f"subj={(fetched.subject or '')[:80]}"
                    )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        finished = _utcnow_iso()
        brain.finish_run(run_id, finished, counts, error=error)

    totals = brain.category_counts()
    print(
        f"[mia_routines] run finished at {finished} — "
        f"new={counts['total']} "
        f"(contas_a_pagar={counts['contas_a_pagar']}, "
        f"contabilidade={counts['contabilidade']}, "
        f"other={counts['other']}) | "
        f"db_totals={totals}"
    )

    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(run())
