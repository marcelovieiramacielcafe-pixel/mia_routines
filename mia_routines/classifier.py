"""Heuristic classifier for accounts-payable / accounting emails.

Two categories are recognised explicitly:
- contas_a_pagar: invoices, payment demands, recurring bills.
- contabilidade: tax authority, accountant, payroll, social security.

Everything else falls through as "other" so the indexer can still store it
without flagging it for the routine's main mission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORY_CONTAS_A_PAGAR = "contas_a_pagar"
CATEGORY_CONTABILIDADE = "contabilidade"
CATEGORY_OTHER = "other"

# Keyword sets — Portuguese-first because that's the user's primary language,
# with English and Spanish fallbacks for foreign suppliers (Hetzner, etc.).
_CONTAS_A_PAGAR_KEYWORDS = (
    "fatura", "factura", "invoice", "rechnung", "facture",
    "recibo", "receipt", "vencimento", "due date", "data limite",
    "pagamento", "payment", "débito direto", "debito directo",
    "cobrança", "cobranca", "boleto",
    "iban", "nib", "transferência", "transferencia", "wire transfer",
    "valor a pagar", "total a pagar", "amount due", "saldo em dívida",
    "subscrição", "subscription", "renovação", "renewal",
)

_CONTABILIDADE_KEYWORDS = (
    "iva", "irs", "irc", "ies",
    "autoridade tributária", "autoridade tributaria", "finanças", "financas",
    "segurança social", "seguranca social",
    "declaração", "declaracao", "declaration",
    "modelo 22", "modelo 3", "modelo 30", "modelo 39", "modelo 44",
    "toc", "contabilista", "técnico oficial de contas",
    "guia de pagamento", "nota de liquidação", "nota de liquidacao",
    "salário", "salario", "vencimentos", "processamento salarial",
    "iva trimestral", "iva mensal",
)

# Senders frequently associated with each category — case-insensitive substring match.
_CONTAS_A_PAGAR_SENDERS = (
    "hetzner.com", "edp.pt", "edp.com", "meo.pt", "vodafone.pt", "nos.pt",
    "galp.pt", "endesa.pt", "iberdrola.pt",
    "amazon.", "aws.", "google.com", "microsoft.com",
    "stripe.com", "paypal.com", "wise.com", "revolut.com",
    "saft", "moloni", "invoicexpress",
)

_CONTABILIDADE_SENDERS = (
    "@at.gov.pt", "portaldasfinancas", "seg-social.pt", "segsocial.pt",
    "@iapmei.pt", "@gov.pt",
)


@dataclass(frozen=True)
class Classification:
    category: str
    score: int
    matched_terms: tuple[str, ...]


def _normalise(text: str | None) -> str:
    return (text or "").lower()


def _count_hits(haystack: str, needles: tuple[str, ...]) -> tuple[int, list[str]]:
    hits: list[str] = []
    for needle in needles:
        if needle in haystack:
            hits.append(needle)
    return len(hits), hits


def classify(*, sender: str | None, subject: str | None, body: str | None) -> Classification:
    """Score the email against each category; return the strongest match.

    Sender matches weigh 3x because they're far more reliable than keyword
    overlap in subject/body — an "iva" mention in a Reddit digest is noise,
    but mail from at.gov.pt is almost always tax-related.
    """
    sender_n = _normalise(sender)
    subject_n = _normalise(subject)
    body_n = _normalise(body)
    haystack = f"{subject_n}\n{body_n}"

    contas_subj, contas_subj_hits = _count_hits(haystack, _CONTAS_A_PAGAR_KEYWORDS)
    contab_subj, contab_subj_hits = _count_hits(haystack, _CONTABILIDADE_KEYWORDS)

    contas_sender, contas_sender_hits = _count_hits(sender_n, _CONTAS_A_PAGAR_SENDERS)
    contab_sender, contab_sender_hits = _count_hits(sender_n, _CONTABILIDADE_SENDERS)

    contas_score = contas_subj + 3 * contas_sender
    contab_score = contab_subj + 3 * contab_sender

    if contab_score == 0 and contas_score == 0:
        return Classification(CATEGORY_OTHER, 0, ())

    if contab_score > contas_score:
        terms = tuple(contab_subj_hits + contab_sender_hits)
        return Classification(CATEGORY_CONTABILIDADE, contab_score, terms)

    terms = tuple(contas_subj_hits + contas_sender_hits)
    return Classification(CATEGORY_CONTAS_A_PAGAR, contas_score, terms)
