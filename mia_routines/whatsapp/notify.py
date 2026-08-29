"""Telegram output. Send-only, on purpose.

There is no `getUpdates`, no webhook, no receive loop anywhere in this package.
Mia reads and warns; she never answers anyone, and she cannot be talked to
through this channel.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_TELEGRAM = 4096


def enviar(token: str, chat_id: str, texto: str) -> bool:
    """Envia para o chat fixo. Devolve False em vez de rebentar a rotina."""
    for pedaco in _partir(texto, LIMITE_TELEGRAM):
        try:
            resposta = requests.post(
                API.format(token=token),
                json={"chat_id": chat_id, "text": pedaco, "parse_mode": "HTML"},
                timeout=15,
            )
            resposta.raise_for_status()
        except requests.RequestException as erro:
            log.error("Telegram falhou: %s", erro)
            return False
    return True


def _partir(texto: str, limite: int) -> list[str]:
    """Parte por linhas para não cortar uma citação a meio."""
    if len(texto) <= limite:
        return [texto]
    pedacos, atual = [], ""
    for linha in texto.split("\n"):
        if len(atual) + len(linha) + 1 > limite:
            if atual:
                pedacos.append(atual)
            atual = linha[:limite]
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        pedacos.append(atual)
    return pedacos
