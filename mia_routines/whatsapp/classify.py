"""Model classification for what the rules could not settle.

Batched: one call per `lote_max` messages, so the instructions are paid for
once instead of once per message. A daily ceiling in the store stops a runaway
day from becoming a runaway bill — past the ceiling everything falls back to
`requer_resposta`, which is the safe direction to be wrong in.
"""

from __future__ import annotations

import json
import logging
import re

from .rules import INFORMATIVO, RESPOSTA, RUIDO, URGENTE

log = logging.getLogger(__name__)

CATEGORIAS_VALIDAS = {URGENTE, RESPOSTA, INFORMATIVO, RUIDO}

INSTRUCOES = """És um triador de mensagens de WhatsApp para o Marcelo, que gere uma
empresa de logística em Portugal e conduz o dia inteiro. Classificas; não respondes
a ninguém e não escreves em nome dele.

Categorias:
- urgente: trabalho a perder-se, cliente parado, avaria ou acidente de viatura,
  prazo que termina hoje.
- requer_resposta: alguém espera resposta dele — convites, propostas de trabalho,
  pedidos diretos — mas não é para já.
- informativo: convém saber, não exige ação.
- ruido: saudações, correntes, figurinhas, conversa de grupo sem conteúdo.

Regras:
- Nunca inventes. Se não perceberes uma mensagem, devolve requer_resposta.
- Julga apenas o texto que te é dado. Não presumas contexto que não está lá.
- Devolve APENAS um array JSON válido, sem texto antes ou depois, sem markdown.

Formato de cada elemento: {"id": <número>, "categoria": "<categoria>"}"""


def classificar(mensagens, cfg, store) -> dict[int, tuple[str, str]]:
    """Classifica um lote. Devolve {id: (categoria, motivo)}.

    Nunca levanta exceção por falha do modelo: em erro, tudo cai em
    `requer_resposta` e o motivo diz porquê.
    """
    if not mensagens:
        return {}

    if store.chamadas_hoje() >= cfg.teto_chamadas_dia:
        log.warning("Teto diário de chamadas atingido (%d).", cfg.teto_chamadas_dia)
        return {m.id: (RESPOSTA, "teto diario atingido") for m in mensagens}

    linhas = "\n".join(
        f'{m.id}. [{m.conversa}] {(m.texto or "").strip()[:400]}' for m in mensagens
    )
    prompt = f"{INSTRUCOES}\n\nMensagens:\n{linhas}"

    try:
        import anthropic

        cliente = anthropic.Anthropic()  # ANTHROPIC_API_KEY do ambiente
        resposta = cliente.messages.create(
            model=cfg.anthropic_model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        store.consumir_chamada(len(mensagens))
        texto = "".join(b.text for b in resposta.content if b.type == "text").strip()
        return _interpretar(texto, mensagens)
    except Exception as erro:  # rede, quota, API, JSON — tudo cai no seguro
        log.error("Classificação falhou (%s). Tudo para requer_resposta.", erro)
        return {m.id: (RESPOSTA, f"falha do modelo: {type(erro).__name__}") for m in mensagens}


def _interpretar(texto: str, mensagens) -> dict[int, tuple[str, str]]:
    encontrado = re.search(r"\[.*\]", texto, re.DOTALL)
    if not encontrado:
        log.error("Resposta sem JSON: %.200s", texto)
        return {m.id: (RESPOSTA, "resposta do modelo ilegivel") for m in mensagens}

    try:
        itens = json.loads(encontrado.group())
    except json.JSONDecodeError:
        log.error("JSON inválido: %.200s", texto)
        return {m.id: (RESPOSTA, "json invalido") for m in mensagens}

    esperados = {m.id for m in mensagens}
    resultado: dict[int, tuple[str, str]] = {}
    for item in itens:
        try:
            mid = int(item["id"])
            categoria = str(item["categoria"]).strip().lower()
        except (KeyError, TypeError, ValueError):
            continue
        if mid in esperados and categoria in CATEGORIAS_VALIDAS:
            resultado[mid] = (categoria, "modelo")

    # O que o modelo não devolveu não fica por classificar em silêncio.
    for mid in esperados - set(resultado):
        resultado[mid] = (RESPOSTA, "omitido pelo modelo")
    return resultado
