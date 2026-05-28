"""Outlook IMAP client wrapper.

Uses imap-tools for ergonomics over the standard library's `imaplib`. Yields
parsed messages; the caller decides how to classify and persist them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from imap_tools import MailBox, AND


@dataclass
class FetchedEmail:
    folder: str
    uid: int
    message_id: str | None
    date_utc: str | None
    sender: str | None
    recipients: str | None
    subject: str | None
    body_text: str | None
    attachments: list[dict]
    parse_error: str | None = None


def _to_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class OutlookIMAP:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    def fetch_since(
        self,
        folder: str,
        *,
        min_uid: int,
        lookback_days: int,
        max_messages: int = 5000,
    ) -> Iterator[FetchedEmail]:
        """Yield messages with UID > min_uid; falls back to a date window
        on the first run (when min_uid == 0) so we don't drag the entire
        mailbox.
        """
        with MailBox(self.host, port=self.port).login(self.user, self.password, initial_folder=folder) as mailbox:
            if min_uid > 0:
                criteria = AND(uid=f"{min_uid + 1}:*")
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
                criteria = AND(date_gte=since)

            count = 0
            for msg in mailbox.fetch(criteria, mark_seen=False, bulk=True):
                if count >= max_messages:
                    break
                count += 1

                try:
                    uid = int(msg.uid) if msg.uid and msg.uid.isdigit() else 0
                except Exception:
                    uid = 0

                # Parsing is lazy in imap-tools: subject/body/attachment access
                # can raise on malformed MIME. Degrade to an error record (still
                # carrying the uid) instead of aborting the whole fetch — that
                # lets the caller advance past a poison message rather than loop.
                try:
                    attachments: list[dict] = []
                    for att in msg.attachments:
                        attachments.append({
                            "filename": att.filename,
                            "content_type": att.content_type,
                            "size_bytes": att.size,
                        })

                    yield FetchedEmail(
                        folder=folder,
                        uid=uid,
                        message_id=msg.headers.get("message-id", [None])[0] if msg.headers else None,
                        date_utc=_to_utc_iso(msg.date),
                        sender=msg.from_,
                        recipients=", ".join(msg.to or ()),
                        subject=msg.subject,
                        body_text=msg.text or msg.html or "",
                        attachments=attachments,
                    )
                except Exception as exc:
                    yield FetchedEmail(
                        folder=folder,
                        uid=uid,
                        message_id=None,
                        date_utc=None,
                        sender=None,
                        recipients=None,
                        subject=None,
                        body_text=None,
                        attachments=[],
                        parse_error=f"{type(exc).__name__}: {exc}",
                    )
