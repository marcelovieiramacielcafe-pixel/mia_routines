"""Deterministic pre-filter. Runs before the model, and decides the urgent case.

Two jobs, in this order:

1. **Triability** — say whether the notification carries text a human could act
   on at all. Grouped ("3 mensagens novas"), media-only ("Mensagem de voz") and
   empty notifications carry no content; recording *why* is what makes the
   three-day test measurable.
2. **Urgency** — decide it by rule, never by model. A crash notification must
   not depend on an API call succeeding.

Everything the rules cannot settle is left as UNKNOWN for the model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Categorias finais. `desconhecido` significa "entrega ao modelo".
URGENTE = "urgente"
RESPOSTA = "requer_resposta"
INFORMATIVO = "informativo"
RUIDO = "ruido"
DESCONHECIDO = "desconhecido"

# Porque é que uma notificação não é triável. Contado no teste dos três dias.
TRIAVEL = "triavel"
AGRUPADA = "agrupada"
SEM_TEXTO = "sem_texto"
MEDIA = "media"

# "3 mensagens novas", "12 new messages", "2 mensagens de 3 conversas"
_AGRUPADA = re.compile(
    r"^\s*\d+\s+(mensage|new message|nachrichten|mensajes)", re.IGNORECASE
)

# Notificações que anunciam media em vez de a transcrever.
_MEDIA = re.compile(
    r"^\s*(?:[\U0001F300-\U0001FAFF☀-➿]\s*)?"
    r"(mensagem de voz|mensagem de aúdio|mensagem de audio|audio|áudio|voice message|"
    r"foto|imagem|photo|image|v[ií]deo|video|gif|autocolante|sticker|documento|document|"
    r"contacto|contact|localiza[çc][ãa]o|location)\s*$",
    re.IGNORECASE,
)

# Saudações e ruído social puro. Só apanha a mensagem *inteira*, nunca um prefixo:
# "bom dia, o camião avariou" não é ruído.
_RUIDO = re.compile(
    r"^\s*(?:bom dia|boa tarde|boa noite|bom fim de semana|obrigado|obrigada|"
    r"de nada|ok|okay|k|sim|n[ãa]o|certo|combinado|abra[çc]o|abra[çc]os|am[ée]n|"
    r"am[ée]m|parab[ée]ns|bom trabalho|👍|🙏|❤️|😂)\s*[.!…]*\s*$",
    re.IGNORECASE,
)

# Matrícula portuguesa: AA-00-AA, 00-AA-00, AA-00-00 e variantes sem hífen.
_MATRICULA = re.compile(
    r"\b(?:[A-Z]{2}[-\s]?\d{2}[-\s]?(?:[A-Z]{2}|\d{2})|\d{2}[-\s]?[A-Z]{2}[-\s]?\d{2})\b"
)


def _normalizar(texto: str) -> str:
    """Lowercase sem acentos, para comparar palavras-chave de forma estável."""
    sem_acentos = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acentos if not unicodedata.combining(c))


@dataclass(frozen=True)
class Decisao:
    categoria: str
    motivo: str
    triabilidade: str

    @property
    def precisa_modelo(self) -> bool:
        return self.categoria == DESCONHECIDO


def triabilidade(texto: str | None) -> str:
    """Classifica a *forma* da notificação, antes de olhar ao conteúdo."""
    if texto is None or not texto.strip():
        return SEM_TEXTO
    if _AGRUPADA.match(texto):
        return AGRUPADA
    if _MEDIA.match(texto):
        return MEDIA
    return TRIAVEL


def _radical_presente(radical: str, texto: str) -> bool:
    """Procura o radical no início de uma palavra, com qualquer terminação.

    As palavras da lista são radicais, não palavras inteiras: "avari" apanha
    avaria, avariou, avariada. Exigir a palavra exata faria a via urgente
    falhar por causa de uma conjugação, que é o pior sítio para falhar.
    """
    return re.search(rf"\b{re.escape(radical)}\w*", texto) is not None


def _e_urgente(conversa: str, texto: str, cfg) -> str | None:
    """Devolve o motivo se a regra de urgência disparar, senão None."""
    conversa_norm = _normalizar(conversa)
    for alvo in cfg.urgente_conversas:
        if _normalizar(alvo) in conversa_norm:
            return f"conversa:{alvo}"

    texto_norm = _normalizar(texto)
    for radical in cfg.urgente_palavras:
        if _radical_presente(_normalizar(radical), texto_norm):
            return f"palavra:{radical}"

    if _MATRICULA.search(texto.upper()):
        return "matricula"
    return None


def decidir(conversa: str, texto: str | None, cfg) -> Decisao:
    """Aplica as regras fixas. `DESCONHECIDO` significa: entrega ao modelo."""
    forma = triabilidade(texto)

    conversa_norm = _normalizar(conversa)
    for alvo in cfg.ignorar_conversas:
        if _normalizar(alvo) in conversa_norm:
            return Decisao(RUIDO, f"conversa ignorada:{alvo}", forma)

    if forma != TRIAVEL:
        # Sem texto não há triagem possível. Não é ruído — é uma falha de
        # captura, e conta como tal no relatório dos três dias.
        return Decisao(INFORMATIVO, f"nao triavel:{forma}", forma)

    texto = texto or ""
    motivo = _e_urgente(conversa, texto, cfg)
    if motivo:
        return Decisao(URGENTE, motivo, forma)

    if _RUIDO.match(texto):
        return Decisao(RUIDO, "saudacao", forma)

    return Decisao(DESCONHECIDO, "modelo", forma)
