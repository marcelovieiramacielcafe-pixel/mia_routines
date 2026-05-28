"""SQLite-backed Second Brain store for indexed emails.

Schema is intentionally narrow: one row per (folder, uid) tuple, plus a
`category` column populated by the classifier. The store is the source of
truth for "what have we already indexed?" — main_routine.py uses
`max_uid_for(folder)` to fetch only new mail on each run.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    folder          TEXT    NOT NULL,
    uid             INTEGER NOT NULL,
    message_id      TEXT,
    date_utc        TEXT,
    sender          TEXT,
    recipients      TEXT,
    subject         TEXT,
    body_text       TEXT,
    body_preview    TEXT,
    attachments     TEXT,
    category        TEXT    NOT NULL,
    classifier_score INTEGER NOT NULL,
    classifier_terms TEXT,
    indexed_at_utc  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (folder, uid)
);

CREATE INDEX IF NOT EXISTS idx_emails_category ON emails (category);
CREATE INDEX IF NOT EXISTS idx_emails_date     ON emails (date_utc);
CREATE INDEX IF NOT EXISTS idx_emails_sender   ON emails (sender);

CREATE TABLE IF NOT EXISTS run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc  TEXT NOT NULL,
    finished_at_utc TEXT,
    new_messages    INTEGER NOT NULL DEFAULT 0,
    contas_a_pagar  INTEGER NOT NULL DEFAULT 0,
    contabilidade   INTEGER NOT NULL DEFAULT 0,
    other           INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
"""


def _sqlite_safe(value):
    """Replace lone surrogates / un-encodable chars so SQLite's UTF-8 writer
    never raises. Attachment filenames and bodies copied straight out of raw
    MIME (e.g. phone-camera route photos) routinely carry these, and an
    unhandled UnicodeEncodeError on INSERT would abort the whole run."""
    if not isinstance(value, str):
        return value
    return value.encode("utf-8", "replace").decode("utf-8")


@dataclass
class StoredEmail:
    folder: str
    uid: int
    message_id: str | None
    date_utc: str | None
    sender: str | None
    recipients: str | None
    subject: str | None
    body_text: str | None
    body_preview: str | None
    attachments: list[dict]
    category: str
    classifier_score: int
    classifier_terms: tuple[str, ...]


class SecondBrain:
    def __init__(self, path: Path):
        self.path = path
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def max_uid_for(self, folder: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(uid), 0) AS max_uid FROM emails WHERE folder = ?",
                (folder,),
            ).fetchone()
            return int(row["max_uid"])

    def insert_email(self, email: StoredEmail) -> bool:
        """Returns True if inserted, False if (folder, uid) already exists."""
        attachments = [
            {k: _sqlite_safe(v) for k, v in att.items()}
            for att in email.attachments
        ]
        terms = [_sqlite_safe(t) for t in email.classifier_terms]
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO emails (
                        folder, uid, message_id, date_utc, sender, recipients,
                        subject, body_text, body_preview, attachments,
                        category, classifier_score, classifier_terms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.folder, email.uid,
                        _sqlite_safe(email.message_id), email.date_utc,
                        _sqlite_safe(email.sender), _sqlite_safe(email.recipients),
                        _sqlite_safe(email.subject),
                        _sqlite_safe(email.body_text), _sqlite_safe(email.body_preview),
                        json.dumps(attachments, ensure_ascii=False),
                        email.category, email.classifier_score,
                        json.dumps(terms, ensure_ascii=False),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def record_failure(
        self,
        folder: str,
        uid: int,
        *,
        message_id: str | None = None,
        date_utc: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        error: str = "",
    ) -> bool:
        """Insert a sentinel row (category='error') for a message we couldn't
        process, so the per-folder UID watermark advances past it.

        Without this, one un-processable message permanently blocks the
        watermark: it gets re-fetched and re-fails on every scheduled run,
        which is exactly the error loop this guards against.
        """
        safe_error = _sqlite_safe(error)
        return self.insert_email(StoredEmail(
            folder=folder,
            uid=uid,
            message_id=message_id,
            date_utc=date_utc,
            sender=sender,
            recipients=None,
            subject=subject,
            body_text=None,
            body_preview=safe_error[:400] if safe_error else None,
            attachments=[],
            category="error",
            classifier_score=0,
            classifier_terms=(safe_error,) if safe_error else (),
        ))

    def start_run(self, started_at_utc: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO run_log (started_at_utc) VALUES (?)",
                (started_at_utc,),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        finished_at_utc: str,
        counts: dict[str, int],
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE run_log
                SET finished_at_utc = ?,
                    new_messages    = ?,
                    contas_a_pagar  = ?,
                    contabilidade   = ?,
                    other           = ?,
                    error           = ?
                WHERE id = ?
                """,
                (
                    finished_at_utc,
                    counts.get("total", 0),
                    counts.get("contas_a_pagar", 0),
                    counts.get("contabilidade", 0),
                    counts.get("other", 0),
                    error,
                    run_id,
                ),
            )

    def category_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) AS n FROM emails GROUP BY category"
            ).fetchall()
            return {row["category"]: int(row["n"]) for row in rows}
