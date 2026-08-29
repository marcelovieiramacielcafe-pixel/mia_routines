"""HTTP ingestion endpoint for forwarded phone notifications.

Deliberately stdlib-only and bound to localhost: this sits behind the Caddy
that already terminates TLS on the VPS, and handles a few hundred requests a
day. Adding a web framework for that load would be cost without benefit.

Authentication is a shared bearer token compared in constant time. The endpoint
is on the public internet through Caddy, so an unauthenticated write would let
anyone inject fake urgent alerts.
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import rules
from .notify import enviar
from .store import Store

log = logging.getLogger(__name__)

# Só notificações do WhatsApp entram. Qualquer outra app é descartada à porta.
PACOTES_ACEITES = {"com.whatsapp", "com.whatsapp.w4b"}

MAX_CORPO = 64 * 1024
JANELA_DUPLICADOS = 90  # segundos


class _Handler(BaseHTTPRequestHandler):
    cfg = None
    store: Store = None
    _recentes: dict[tuple[str, str], float] = {}

    def do_GET(self) -> None:  # noqa: N802 (assinatura da stdlib)
        if self.path == "/saude":
            self._responder(200, {"estado": "vivo"})
        else:
            self._responder(404, {"erro": "nao encontrado"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/notificacao":
            self._responder(404, {"erro": "nao encontrado"})
            return
        if not self._autorizado():
            log.warning("Pedido sem autorização de %s", self.client_address[0])
            self._responder(401, {"erro": "nao autorizado"})
            return

        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._responder(400, {"erro": "content-length invalido"})
            return
        if tamanho <= 0 or tamanho > MAX_CORPO:
            self._responder(413, {"erro": "corpo vazio ou grande demais"})
            return

        try:
            dados = json.loads(self.rfile.read(tamanho))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._responder(400, {"erro": "json invalido"})
            return
        if not isinstance(dados, dict):
            self._responder(400, {"erro": "esperado um objeto json"})
            return

        self._responder(*self._processar(dados))

    # ------------------------------------------------------------------ interno

    def _autorizado(self) -> bool:
        cabecalho = self.headers.get("Authorization", "")
        prefixo = "Bearer "
        if not cabecalho.startswith(prefixo):
            return False
        return hmac.compare_digest(cabecalho[len(prefixo):], self.cfg.ingest_token)

    def _processar(self, dados: dict) -> tuple[int, dict]:
        app = str(dados.get("app") or dados.get("packageName") or "").strip()
        if app and app not in PACOTES_ACEITES:
            return 200, {"estado": "ignorado", "razao": "app nao aceite"}

        conversa = str(dados.get("conversa") or dados.get("title") or "").strip()
        texto_bruto = dados.get("texto", dados.get("text"))
        texto = str(texto_bruto).strip() if texto_bruto is not None else None
        if not conversa:
            return 400, {"erro": "falta a conversa (title)"}

        if self._duplicado(conversa, texto or ""):
            return 200, {"estado": "ignorado", "razao": "duplicado"}

        decisao = rules.decidir(conversa, texto, self.cfg)

        avisado = False
        if decisao.categoria == rules.URGENTE:
            # Enviado a partir da memória, antes de qualquer escrita em disco:
            # o aviso urgente não depende de a base de dados estar saudável.
            avisado = enviar(
                self.cfg.telegram_token,
                self.cfg.telegram_chat_id,
                f"🔴 <b>{_escapar(conversa)}</b>\n{_escapar(texto or '')}",
            )

        self.store.guardar(
            conversa=conversa,
            app=app or "desconhecido",
            texto=texto,
            triabilidade=decisao.triabilidade,
            categoria=decisao.categoria,
            motivo=decisao.motivo,
            avisado=avisado,
            reter_horas=self.cfg.reter_horas,
            guardar_texto=self.cfg.guardar_texto,
        )
        return 200, {"estado": "aceite", "categoria": decisao.categoria}

    def _duplicado(self, conversa: str, texto: str) -> bool:
        """O Android reemite notificações ao atualizá-las; não avisamos duas vezes."""
        import time

        agora = time.monotonic()
        chave = (conversa, texto)
        for antiga, quando in list(self._recentes.items()):
            if agora - quando > JANELA_DUPLICADOS:
                del self._recentes[antiga]
        if chave in self._recentes:
            return True
        self._recentes[chave] = agora
        return False

    def _responder(self, codigo: int, corpo: dict) -> None:
        payload = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, formato: str, *args) -> None:
        # Nunca registar o corpo do pedido: contém mensagens de terceiros.
        log.info("%s %s", self.client_address[0], formato % args)


def _escapar(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def servir(cfg, store: Store) -> None:
    _Handler.cfg = cfg
    _Handler.store = store
    servidor = ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), _Handler)
    log.info("Triagem à escuta em %s:%d", cfg.bind_host, cfg.bind_port)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("A terminar.")
    finally:
        servidor.server_close()
