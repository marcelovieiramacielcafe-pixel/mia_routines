"""Entry point for the Washu Telegram bot (long-running process).

Loads config from env, builds the python-telegram-bot Application, and
starts long-polling. This is NOT meant to run on Claude Routines / cron
— it's a persistent service. Host on a VPS, fly.io, Railway, etc.
"""

from __future__ import annotations

import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from mia_routines.washu import BotConfig, build_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    cfg = BotConfig.from_env()
    app = build_app(cfg)

    print(f"[washu_bot] long-polling started (model={cfg.model}, db={cfg.db_path})")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
