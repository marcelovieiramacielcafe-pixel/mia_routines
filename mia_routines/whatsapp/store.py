"""SQLite store with a hard retention limit.

Two design decisions worth stating, because they are the answer to "estas
mensagens são de outras pessoas":

* The original text is wiped after `reter_horas` (48 by default). The row
  survives with its classification, so statistics keep working while the words
  of third parties do not linger.
* With `guardar_texto=False` the text is never written at all. Urgent alerts
  still work (they are sent from memory, before storage); the daily digest then
  names the conversation without quoting it.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS mensagens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recebido_utc  TEXT    NOT NULL,
    conversa      TEXT    NOT NULL,
    app           TEXT    NOT NULL,
    texto         TEXT,
    triabilidade  TEXT    NOT NULL,
    categoria     TEXT    NOT NULL,
    motivo        TEXT    NOT NULL,
    avisado       INTEGER NOT NULL DEFAULT 0,
    no_resumo     INTEGER NOT NULL DEFAULT 0,
    expira_utc    TEXT    NOT NULL,
    texto_apagado INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_mensagens_expira ON mensagens(expira_utc);
CREATE INDEX IF NOT EXISTS ix_mensagens_resumo ON mensagens(no_resumo, categoria);

CREATE TABLE IF NOT EXISTS orcamento (
    dia       TEXT PRIMARY KEY,
    chamadas  INTEGER NOT NULL DEFAULT 0,
    mensagens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pulso (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    ultima_utc  TEXT NOT NULL,
    avisado_utc TEXT
);
"""


def agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Mensagem:
    id: int
    conversa: str
    texto: str | None
    categoria: str
    motivo: str
    recebido_utc: str
    texto_apagado: bool


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ escrita

    def guardar(
        self,
        *,
        conversa: str,
        app: str,
        texto: str | None,
        triabilidade: str,
        categoria: str,
        motivo: str,
        avisado: bool,
        reter_horas: int,
        guardar_texto: bool,
    ) -> int:
        recebido = agora()
        expira = recebido + timedelta(hours=reter_horas)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO mensagens
                   (recebido_utc, conversa, app, texto, triabilidade, categoria,
                    motivo, avisado, expira_utc, texto_apagado)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    _iso(recebido), conversa, app,
                    texto if guardar_texto else None,
                    triabilidade, categoria, motivo,
                    1 if avisado else 0, _iso(expira),
                    0 if guardar_texto else 1,
                ),
            )
            conn.execute(
                """INSERT INTO pulso (id, ultima_utc) VALUES (1, ?)
                   ON CONFLICT(id) DO UPDATE SET ultima_utc = excluded.ultima_utc,
                                                 avisado_utc = NULL""",
                (_iso(recebido),),
            )
            return int(cur.lastrowid)

    def reclassificar(self, ids_categorias: dict[int, tuple[str, str]]) -> None:
        """Aplica as categorias devolvidas pelo modelo."""
        if not ids_categorias:
            return
        with self._conn() as conn:
            conn.executemany(
                "UPDATE mensagens SET categoria = ?, motivo = ? WHERE id = ?",
                [(cat, mot, mid) for mid, (cat, mot) in ids_categorias.items()],
            )

    def marcar_no_resumo(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn() as conn:
            conn.executemany(
                "UPDATE mensagens SET no_resumo = 1 WHERE id = ?", [(i,) for i in ids]
            )

    # ------------------------------------------------------------------ leitura

    def por_classificar(self, limite: int) -> list[Mensagem]:
        with self._conn() as conn:
            linhas = conn.execute(
                """SELECT * FROM mensagens WHERE categoria = 'desconhecido'
                   ORDER BY id LIMIT ?""",
                (limite,),
            ).fetchall()
        return [self._para_mensagem(l) for l in linhas]

    def para_resumo(self) -> list[Mensagem]:
        with self._conn() as conn:
            linhas = conn.execute(
                """SELECT * FROM mensagens
                   WHERE no_resumo = 0 AND categoria IN ('urgente','requer_resposta','informativo')
                   ORDER BY conversa, id"""
            ).fetchall()
        return [self._para_mensagem(l) for l in linhas]

    def contagem_ruido(self) -> int:
        with self._conn() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM mensagens WHERE no_resumo = 0 AND categoria = 'ruido'"
            ).fetchone()[0])

    def contagem_triabilidade(self) -> dict[str, int]:
        """Quantas notificações vieram sem texto utilizável, e de que forma."""
        with self._conn() as conn:
            linhas = conn.execute(
                "SELECT triabilidade, COUNT(*) c FROM mensagens GROUP BY triabilidade"
            ).fetchall()
        return {l["triabilidade"]: int(l["c"]) for l in linhas}

    @staticmethod
    def _para_mensagem(linha: sqlite3.Row) -> Mensagem:
        return Mensagem(
            id=int(linha["id"]),
            conversa=linha["conversa"],
            texto=linha["texto"],
            categoria=linha["categoria"],
            motivo=linha["motivo"],
            recebido_utc=linha["recebido_utc"],
            texto_apagado=bool(linha["texto_apagado"]),
        )

    # ----------------------------------------------------------------- orçamento

    def consumir_chamada(self, mensagens: int) -> None:
        dia = agora().strftime("%Y-%m-%d")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO orcamento (dia, chamadas, mensagens) VALUES (?, 1, ?)
                   ON CONFLICT(dia) DO UPDATE SET chamadas = chamadas + 1,
                                                  mensagens = mensagens + excluded.mensagens""",
                (dia, mensagens),
            )

    def chamadas_hoje(self) -> int:
        dia = agora().strftime("%Y-%m-%d")
        with self._conn() as conn:
            linha = conn.execute(
                "SELECT chamadas FROM orcamento WHERE dia = ?", (dia,)
            ).fetchone()
        return int(linha["chamadas"]) if linha else 0

    # ------------------------------------------------------------------- limpeza

    def apagar_textos_expirados(self) -> int:
        """Apaga o texto original passado o prazo. A classificação fica."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE mensagens SET texto = NULL, texto_apagado = 1
                   WHERE texto_apagado = 0 AND expira_utc < ?""",
                (_iso(agora()),),
            )
            return cur.rowcount

    # -------------------------------------------------------------------- pulso

    def ultima_recebida(self) -> datetime | None:
        with self._conn() as conn:
            linha = conn.execute("SELECT ultima_utc FROM pulso WHERE id = 1").fetchone()
        if not linha:
            return None
        return datetime.strptime(linha["ultima_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )

    def pulso_ja_avisado(self) -> bool:
        with self._conn() as conn:
            linha = conn.execute("SELECT avisado_utc FROM pulso WHERE id = 1").fetchone()
        return bool(linha and linha["avisado_utc"])

    def marcar_pulso_avisado(self) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE pulso SET avisado_utc = ? WHERE id = 1", (_iso(agora()),))
