"""Daily digest, heartbeat and retention sweep — the scheduled half.

The digest quotes the original message, as asked. When `guardar_texto` is off,
or the text has already passed its retention window, it names the conversation
and says the text is gone rather than inventing a summary of it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from . import rules
from .classify import classificar
from .notify import enviar
from .store import Store, agora

log = logging.getLogger(__name__)

ETIQUETAS = {
    rules.URGENTE: "🔴 Urgente",
    rules.RESPOSTA: "🟠 Requer resposta",
    rules.INFORMATIVO: "⚪ Informativo",
}
ORDEM = [rules.URGENTE, rules.RESPOSTA, rules.INFORMATIVO]


def escoar_fila(cfg, store: Store) -> int:
    """Classifica tudo o que está à espera do modelo, em lotes. Devolve o total."""
    total = 0
    while True:
        lote = store.por_classificar(cfg.lote_max)
        if not lote:
            return total
        store.reclassificar(classificar(lote, cfg, store))
        total += len(lote)
        if total >= cfg.lote_max * 50:  # trava de segurança
            log.warning("Fila muito longa; o resto fica para a próxima passagem.")
            return total


def construir_resumo(cfg, store: Store) -> tuple[str, list[int]]:
    mensagens = store.para_resumo()
    ruido = store.contagem_ruido()
    if not mensagens:
        return "", []

    por_categoria = defaultdict(lambda: defaultdict(list))
    for m in mensagens:
        por_categoria[m.categoria][m.conversa].append(m)

    linhas = [f"📋 <b>Resumo do dia</b> — {agora().strftime('%d/%m/%Y')}", ""]
    for categoria in ORDEM:
        conversas = por_categoria.get(categoria)
        if not conversas:
            continue
        linhas.append(f"<b>{ETIQUETAS[categoria]}</b>")
        for conversa, itens in conversas.items():
            linhas.append(f"  <i>{_escapar(conversa)}</i>")
            for m in itens:
                linhas.append(f"    • {_citar(m)}")
        linhas.append("")

    linhas.append(f"<i>{ruido} mensagens classificadas como ruído e não mostradas.</i>")

    formas = store.contagem_triabilidade()
    perdidas = sum(v for k, v in formas.items() if k != rules.TRIAVEL)
    if perdidas:
        detalhe = ", ".join(f"{k}: {v}" for k, v in sorted(formas.items()) if k != rules.TRIAVEL)
        linhas.append(f"<i>{perdidas} notificações chegaram sem texto utilizável ({detalhe}).</i>")

    return "\n".join(linhas), [m.id for m in mensagens]


def _citar(m) -> str:
    if m.texto:
        texto = m.texto if len(m.texto) <= 300 else m.texto[:297] + "…"
        return _escapar(texto)
    if m.texto_apagado:
        return "<i>(texto não guardado — vê no WhatsApp)</i>"
    return "<i>(sem texto na notificação)</i>"


def enviar_resumo(cfg, store: Store) -> bool:
    escoar_fila(cfg, store)
    texto, ids = construir_resumo(cfg, store)
    if not texto:
        log.info("Nada para resumir.")
        return True
    if enviar(cfg.telegram_token, cfg.telegram_chat_id, texto):
        store.marcar_no_resumo(ids)
        return True
    log.error("Resumo não foi entregue; fica por marcar para nova tentativa.")
    return False


def verificar_pulso(cfg, store: Store) -> bool:
    """Avisa se o telemóvel deixou de reencaminhar durante o horário útil.

    Sem isto, a falha mais provável do sistema — telemóvel sem bateria, serviço
    morto pelo Android — é indistinguível de um dia sossegado.
    """
    momento = agora()
    if not (cfg.pulso_inicio <= momento.hour < cfg.pulso_fim):
        return True

    ultima = store.ultima_recebida()
    if ultima is None:
        return True
    if momento - ultima <= timedelta(hours=cfg.pulso_horas):
        return True
    if store.pulso_ja_avisado():
        return True

    horas = (momento - ultima).total_seconds() / 3600
    enviado = enviar(
        cfg.telegram_token,
        cfg.telegram_chat_id,
        f"⚠️ Não recebo nada do telemóvel há {horas:.1f} horas "
        f"(última às {ultima.strftime('%H:%M')} UTC). Vê se a app de "
        f"reencaminhamento continua a correr.",
    )
    if enviado:
        store.marcar_pulso_avisado()
    return enviado


def limpar(store: Store) -> int:
    apagados = store.apagar_textos_expirados()
    if apagados:
        log.info("Texto apagado em %d mensagens fora do prazo.", apagados)
    return apagados


def _escapar(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
