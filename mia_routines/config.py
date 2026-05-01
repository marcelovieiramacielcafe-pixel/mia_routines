"""Environment-driven configuration. Never hardcode secrets here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Config:
    outlook_user: str
    outlook_app_password: str
    imap_host: str
    imap_port: int
    second_brain_db: Path
    initial_lookback_days: int
    folders: tuple[str, ...]
    verbose: bool

    @classmethod
    def from_env(cls) -> "Config":
        user = os.environ.get("OUTLOOK_USER", "").strip()
        pwd = os.environ.get("OUTLOOK_APP_PASSWORD", "").strip()
        if not user or not pwd:
            raise RuntimeError(
                "OUTLOOK_USER and OUTLOOK_APP_PASSWORD must be set. "
                "Copy .env.example to .env and fill them in."
            )

        db_path = Path(os.environ.get("SECOND_BRAIN_DB", "./data/second_brain.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        folders_raw = os.environ.get("IMAP_FOLDERS", "INBOX")
        folders = tuple(f.strip() for f in folders_raw.split(",") if f.strip())

        return cls(
            outlook_user=user,
            outlook_app_password=pwd,
            imap_host=os.environ.get("OUTLOOK_IMAP_HOST", "outlook.office365.com"),
            imap_port=int(os.environ.get("OUTLOOK_IMAP_PORT", "993")),
            second_brain_db=db_path,
            initial_lookback_days=int(os.environ.get("INITIAL_LOOKBACK_DAYS", "180")),
            folders=folders,
            verbose=os.environ.get("VERBOSE", "0") == "1",
        )
