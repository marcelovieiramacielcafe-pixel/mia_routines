"""Environment-driven configuration for the triage service.

Never hardcode secrets here. Everything comes from the environment, which on
the VPS means a `.env` file readable only by the service user.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _lista(raw: str) -> tuple[str, ...]:
    """Split a comma-separated env var, lowercased and stripped."""
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class TriageConfig:
    # --- ingestão ---
    bind_host: str
    bind_port: int
    ingest_token: str

    # --- armazenamento ---
    db_path: Path
    reter_horas: int
    guardar_texto: bool

    # --- Telegram (só saída) ---
    telegram_token: str
    telegram_chat_id: str

    # --- modelo ---
    anthropic_model: str
    lote_max: int
    teto_chamadas_dia: int

    # --- regras ---
    urgente_conversas: tuple[str, ...] = field(default_factory=tuple)
    urgente_palavras: tuple[str, ...] = field(default_factory=tuple)
    ignorar_conversas: tuple[str, ...] = field(default_factory=tuple)

    # --- pulsação ---
    pulso_horas: int = 3
    pulso_inicio: int = 8
    pulso_fim: int = 21

    @classmethod
    def from_env(cls) -> "TriageConfig":
        token = os.environ.get("TRIAGEM_INGEST_TOKEN", "").strip()
        if len(token) < 32:
            raise RuntimeError(
                "TRIAGEM_INGEST_TOKEN must be set and at least 32 characters. "
                "Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(32))\""
            )

        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not tg_token or not tg_chat:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")

        db_path = Path(os.environ.get("TRIAGEM_DB", "./data/triagem.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            bind_host=os.environ.get("TRIAGEM_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.environ.get("TRIAGEM_BIND_PORT", "8099")),
            ingest_token=token,
            db_path=db_path,
            reter_horas=int(os.environ.get("TRIAGEM_RETER_HORAS", "48")),
            guardar_texto=os.environ.get("TRIAGEM_GUARDAR_TEXTO", "1") == "1",
            telegram_token=tg_token,
            telegram_chat_id=tg_chat,
            anthropic_model=os.environ.get("TRIAGEM_MODELO", "claude-haiku-4-5"),
            lote_max=int(os.environ.get("TRIAGEM_LOTE_MAX", "20")),
            teto_chamadas_dia=int(os.environ.get("TRIAGEM_TETO_CHAMADAS_DIA", "200")),
            urgente_conversas=_lista(os.environ.get("TRIAGEM_URGENTE_CONVERSAS", "")),
            urgente_palavras=_lista(os.environ.get(
                "TRIAGEM_URGENTE_PALAVRAS",
                # Radicais, não palavras inteiras — ver rules._radical_presente.
                "acident,avari,sinistr,reboqu,reboc,guinch,parado na,bateu,despist,"
                "seguradora,oficina,multa,apreendid,urgente,urgencia",
            )),
            ignorar_conversas=_lista(os.environ.get("TRIAGEM_IGNORAR_CONVERSAS", "")),
            pulso_horas=int(os.environ.get("TRIAGEM_PULSO_HORAS", "3")),
            pulso_inicio=int(os.environ.get("TRIAGEM_PULSO_INICIO", "8")),
            pulso_fim=int(os.environ.get("TRIAGEM_PULSO_FIM", "21")),
        )
