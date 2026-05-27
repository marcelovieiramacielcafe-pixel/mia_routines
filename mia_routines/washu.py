"""Washu — Telegram bot that talks to the user as a personal Engineering
Assistant, with read access to the Second Brain SQLite store via tool use.

Architecture: long-polling Telegram bot (python-telegram-bot) -> per-user
in-memory conversation history -> Anthropic Claude API (Opus 4.7, adaptive
thinking) with tools that query the indexed emails DB.

This is a separate, long-running process from main_routine.py. Run with
`python washu_bot.py` on infra that stays up (VPS, Railway, fly.io).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


log = logging.getLogger("washu")


WASHU_SYSTEM = """Tu és a Washu, Engenheira de Sistemas e assistente pessoal do Marcelo. \
Falas português (PT-PT, com naturalidade) e respondes pelo Telegram.

A tua missão é ajudar o Marcelo a gerir o "Segundo Cérebro" — uma base de \
dados SQLite com emails do Outlook indexados e classificados em três categorias:

- `contas_a_pagar` — facturas, lembretes de pagamento, billing recorrente \
(Hetzner, EDP, MEO, Stripe, AWS, etc.)
- `contabilidade` — comunicações da AT, Segurança Social, IVA/IRS/IES, \
processamento salarial, contabilista
- `other` — fallback; ainda indexado mas fora da missão principal

Tens acesso a ferramentas para consultar o Segundo Cérebro. Usa-as quando \
precisares de informação real sobre os emails — nunca inventes contas, \
remetentes, valores, ou datas. Se não tiveres certeza, consulta primeiro.

Estilo:
- Directa, concisa, sem floreios.
- Usa formatação simples (sem markdown complexo — o Telegram não renderiza \
bem). Para emfase usa *asteriscos* (negrito) ou _underscores_ (itálico).
- Quando listares emails, mostra: data curta, remetente (só o domínio se \
ajudar), assunto truncado. Não dumps a body inteira a não ser que peçam.
- Respostas longas: corta o que não é essencial. Se for >2000 caracteres, \
faz resumo + oferece ver detalhes.

O Marcelo trata-te por "Washu". Não te apresentes a cada mensagem."""


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_emails",
        "description": (
            "Pesquisa emails no Segundo Cérebro. Combina filtros opcionais "
            "(query no assunto/corpo, categoria, remetente). Retorna lista "
            "ordenada por data desc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Texto a procurar em subject + body_text (LIKE %query%). Vazio = sem filtro de texto.",
                },
                "category": {
                    "type": "string",
                    "enum": ["contas_a_pagar", "contabilidade", "other"],
                    "description": "Filtra por categoria. Omite para todas.",
                },
                "sender_like": {
                    "type": "string",
                    "description": "Filtro LIKE no sender (ex: '%hetzner%').",
                },
                "since_days": {
                    "type": "integer",
                    "description": "Só emails dos últimos N dias. Omite para sem limite temporal.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de resultados (default 10, max 50).",
                },
            },
        },
    },
    {
        "name": "get_email_detail",
        "description": "Devolve o corpo completo (body_text) de um email pelo seu id interno.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "integer", "description": "id da tabela emails"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "category_summary",
        "description": (
            "Contagem total de emails por categoria + último run. Útil para "
            "responder 'quantos emails tenho de contas_a_pagar?' sem listar."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "recent_runs",
        "description": "Últimas N invocações do main_routine, com contadores e erro (se houver).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Default 5, max 20."},
            },
        },
    },
]


def execute_tool(name: str, tool_input: dict[str, Any], db_path: Path) -> str:
    """Run a tool against the Second Brain SQLite. Returns JSON-encoded result."""
    try:
        if name == "search_emails":
            return _search_emails(db_path, **tool_input)
        if name == "get_email_detail":
            return _get_email_detail(db_path, tool_input["email_id"])
        if name == "category_summary":
            return _category_summary(db_path)
        if name == "recent_runs":
            return _recent_runs(db_path, tool_input.get("limit", 5))
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
    return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _search_emails(
    db_path: Path,
    query: str | None = None,
    category: str | None = None,
    sender_like: str | None = None,
    since_days: int | None = None,
    limit: int = 10,
) -> str:
    limit = max(1, min(int(limit or 10), 50))
    where = []
    params: list[Any] = []

    if query:
        where.append("(subject LIKE ? OR body_text LIKE ?)")
        like = f"%{query}%"
        params += [like, like]
    if category:
        where.append("category = ?")
        params.append(category)
    if sender_like:
        where.append("sender LIKE ?")
        params.append(sender_like if "%" in sender_like else f"%{sender_like}%")
    if since_days:
        where.append("date_utc >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")

    sql = "SELECT id, folder, date_utc, sender, subject, category, body_preview FROM emails"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date_utc DESC LIMIT ?"
    params.append(limit)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    results = [
        {
            "id": r["id"],
            "date_utc": r["date_utc"],
            "sender": r["sender"],
            "subject": r["subject"],
            "category": r["category"],
            "preview": (r["body_preview"] or "")[:200],
        }
        for r in rows
    ]
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False)


def _get_email_detail(db_path: Path, email_id: int) -> str:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, folder, date_utc, sender, recipients, subject, body_text, "
            "category, classifier_score, classifier_terms FROM emails WHERE id = ?",
            (int(email_id),),
        ).fetchone()
    if not row:
        return json.dumps({"error": f"email {email_id} not found"}, ensure_ascii=False)
    body = row["body_text"] or ""
    if len(body) > 8000:
        body = body[:8000] + "\n[... truncado, body total {} chars ...]".format(len(row["body_text"]))
    return json.dumps(
        {
            "id": row["id"],
            "folder": row["folder"],
            "date_utc": row["date_utc"],
            "sender": row["sender"],
            "recipients": row["recipients"],
            "subject": row["subject"],
            "category": row["category"],
            "classifier_score": row["classifier_score"],
            "classifier_terms": row["classifier_terms"],
            "body_text": body,
        },
        ensure_ascii=False,
    )


def _category_summary(db_path: Path) -> str:
    with _connect(db_path) as conn:
        cats = conn.execute(
            "SELECT category, COUNT(*) AS n FROM emails GROUP BY category"
        ).fetchall()
        last_run = conn.execute(
            "SELECT started_at_utc, finished_at_utc, new_messages, "
            "contas_a_pagar, contabilidade, other, error "
            "FROM run_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return json.dumps(
        {
            "totals": {r["category"]: int(r["n"]) for r in cats},
            "last_run": dict(last_run) if last_run else None,
        },
        ensure_ascii=False,
    )


def _recent_runs(db_path: Path, limit: int = 5) -> str:
    limit = max(1, min(int(limit or 5), 20))
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, started_at_utc, finished_at_utc, new_messages, "
            "contas_a_pagar, contabilidade, other, error "
            "FROM run_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@dataclass
class UserState:
    history: list[dict[str, Any]] = field(default_factory=list)


def _trim_history(history: list[dict[str, Any]], max_turns: int = 30) -> None:
    """Drop oldest turns when history exceeds max_turns user+assistant pairs.

    Keeps message structure intact (never cuts off a tool_use without its
    matching tool_result on the next turn).
    """
    if len(history) <= max_turns * 2:
        return
    drop = len(history) - max_turns * 2
    while drop > 0 and history:
        first = history[0]
        history.pop(0)
        drop -= 1
        if first["role"] == "assistant" and any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            or hasattr(b, "type") and b.type == "tool_use"
            for b in (first["content"] if isinstance(first["content"], list) else [])
        ):
            if history and history[0]["role"] == "user":
                history.pop(0)
                drop -= 1


async def respond(
    user_text: str,
    history: list[dict[str, Any]],
    db_path: Path,
    client: anthropic.AsyncAnthropic,
    model: str = "claude-opus-4-7",
) -> str:
    """Manual agentic loop: user message -> tool calls -> final text reply."""
    history.append({"role": "user", "content": user_text})

    final_text = ""
    for _ in range(10):  # cap tool-use rounds defensively
        response = await client.messages.create(
            model=model,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": WASHU_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            thinking={"type": "adaptive"},
            messages=history,
        )

        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            final_text = "\n".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, dict(block.input), db_path)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            history.append({"role": "user", "content": tool_results})
            continue

        # refusal, max_tokens, pause_turn — surface what we have
        final_text = "\n".join(
            b.text for b in response.content if b.type == "text"
        ).strip()
        if response.stop_reason == "refusal":
            final_text = final_text or "[Recusei responder a este pedido.]"
        elif response.stop_reason == "max_tokens":
            final_text += "\n\n[Resposta cortada — atingi o limite de tokens.]"
        break

    _trim_history(history)
    return final_text or "[Sem resposta gerada.]"


def _is_authorized(user_id: int, allowed: frozenset[int]) -> bool:
    return not allowed or user_id in allowed


async def _start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = ctx.bot_data["cfg"]
    if not _is_authorized(update.effective_user.id, cfg.authorized_user_ids):
        await update.message.reply_text("Não autorizado.")
        return
    ctx.bot_data["users"].setdefault(update.effective_user.id, UserState())
    await update.message.reply_text(
        "Olá. Estou cá. Pergunta o que precisares sobre os teus emails — ou "
        "qualquer outra coisa.\n\nComandos: /reset (limpa o histórico desta "
        "conversa)."
    )


async def _reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = ctx.bot_data["cfg"]
    if not _is_authorized(update.effective_user.id, cfg.authorized_user_ids):
        return
    ctx.bot_data["users"][update.effective_user.id] = UserState()
    await update.message.reply_text("Histórico limpo.")


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = ctx.bot_data["cfg"]
    user_id = update.effective_user.id

    if not _is_authorized(user_id, cfg.authorized_user_ids):
        log.warning("unauthorized message from %s (%s)", user_id, update.effective_user.username)
        await update.message.reply_text("Não autorizado.")
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    state = ctx.bot_data["users"].setdefault(user_id, UserState())
    client: anthropic.AsyncAnthropic = ctx.bot_data["client"]

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        reply = await respond(text, state.history, cfg.db_path, client, model=cfg.model)
    except anthropic.APIError as exc:
        log.exception("anthropic api error")
        await update.message.reply_text(f"[Erro da API Claude: {type(exc).__name__}]")
        return
    except Exception as exc:
        log.exception("unexpected error in handler")
        await update.message.reply_text(f"[Erro: {type(exc).__name__}: {exc}]")
        return

    for chunk in _split_for_telegram(reply):
        await update.message.reply_text(chunk)


def _split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """Telegram caps messages at 4096 chars. Split on paragraph boundaries
    where possible.
    """
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(remaining)
    return out


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    anthropic_api_key: str
    db_path: Path
    authorized_user_ids: frozenset[int]
    model: str = "claude-opus-4-7"

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN must be set.")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set.")

        db_path = Path(os.environ.get("SECOND_BRAIN_DB", "./data/second_brain.db"))

        ids_raw = os.environ.get("TELEGRAM_AUTHORIZED_USER_IDS", "").strip()
        ids = frozenset(int(x) for x in ids_raw.split(",") if x.strip())
        if not ids:
            log.warning(
                "TELEGRAM_AUTHORIZED_USER_IDS is empty — bot will reply to ANYONE. "
                "Set this to your Telegram user_id to lock it down."
            )

        return cls(
            telegram_token=token,
            anthropic_api_key=api_key,
            db_path=db_path,
            authorized_user_ids=ids,
            model=os.environ.get("WASHU_MODEL", "claude-opus-4-7"),
        )


def build_app(cfg: BotConfig) -> Application:
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["client"] = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    app.bot_data["users"] = {}

    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("reset", _reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    return app
