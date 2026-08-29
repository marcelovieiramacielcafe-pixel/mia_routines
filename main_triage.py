#!/usr/bin/env python3
"""Entry point for the WhatsApp triage service.

    python main_triage.py servir      # ingestão (contínuo, atrás do Caddy)
    python main_triage.py resumo      # resumo diário  — cron às 20:00
    python main_triage.py pulso       # alarme de silêncio — cron de hora a hora
    python main_triage.py limpar      # apaga texto fora do prazo — cron de hora a hora
"""

from __future__ import annotations

import logging
import sys

from mia_routines.whatsapp.config import TriageConfig
from mia_routines.whatsapp.digest import enviar_resumo, limpar, verificar_pulso
from mia_routines.whatsapp.server import servir
from mia_routines.whatsapp.store import Store

COMANDOS = ("servir", "resumo", "pulso", "limpar")


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    comando = argv[1] if len(argv) > 1 else ""
    if comando not in COMANDOS:
        print(__doc__)
        return 2

    cfg = TriageConfig.from_env()
    store = Store(cfg.db_path)

    if comando == "servir":
        servir(cfg, store)
        return 0
    if comando == "resumo":
        return 0 if enviar_resumo(cfg, store) else 1
    if comando == "pulso":
        return 0 if verificar_pulso(cfg, store) else 1
    limpar(store)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
