"""Tests for the WhatsApp triage service.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from mia_routines.whatsapp import rules
from mia_routines.whatsapp.config import TriageConfig
from mia_routines.whatsapp.store import Store, _iso, agora

TOKEN = "x" * 40


def _cfg(**alteracoes) -> TriageConfig:
    base = TriageConfig(
        bind_host="127.0.0.1",
        bind_port=0,
        ingest_token=TOKEN,
        db_path=Path("/tmp/nao-usado.db"),
        reter_horas=48,
        guardar_texto=True,
        telegram_token="tok",
        telegram_chat_id="123",
        anthropic_model="claude-haiku-4-5",
        lote_max=20,
        teto_chamadas_dia=200,
        urgente_conversas=("bruno figueiredo", "dpd"),
        urgente_palavras=("acident", "avari", "reboc", "reboqu"),
        ignorar_conversas=("promocoes",),
    )
    return replace(base, **alteracoes)


class TestTriabilidade(unittest.TestCase):
    def test_agrupada_nao_e_triavel(self):
        for texto in ("3 mensagens novas", "12 new messages", "2 mensagens de 3 chats"):
            self.assertEqual(rules.triabilidade(texto), rules.AGRUPADA, texto)

    def test_media_nao_e_triavel(self):
        for texto in ("Mensagem de voz", "🎤 Mensagem de voz", "Foto", "Vídeo", "Documento"):
            self.assertEqual(rules.triabilidade(texto), rules.MEDIA, texto)

    def test_vazio_nao_e_triavel(self):
        self.assertEqual(rules.triabilidade(None), rules.SEM_TEXTO)
        self.assertEqual(rules.triabilidade("   "), rules.SEM_TEXTO)

    def test_texto_normal_e_triavel(self):
        self.assertEqual(rules.triabilidade("Podes ligar-me amanhã?"), rules.TRIAVEL)

    def test_mensagem_sobre_fotografia_nao_e_confundida_com_media(self):
        # "Foto" sozinho é media; uma frase que fala de fotos é texto a sério.
        self.assertEqual(rules.triabilidade("Manda a foto da fatura"), rules.TRIAVEL)


class TestUrgencia(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()

    def test_conversa_na_lista_dispara(self):
        d = rules.decidir("Bruno Figueiredo", "amanhã às 8", self.cfg)
        self.assertEqual(d.categoria, rules.URGENTE)

    def test_conversa_sem_acentos_tambem_dispara(self):
        d = rules.decidir("DPD Portugal", "rota alterada", self.cfg)
        self.assertEqual(d.categoria, rules.URGENTE)

    def test_palavra_chave_dispara(self):
        d = rules.decidir("Grupo Família", "o carro teve uma avaria na A1", self.cfg)
        self.assertEqual(d.categoria, rules.URGENTE)

    def test_radical_apanha_as_flexoes(self):
        for texto in ("o camião avariou", "carrinha avariada", "houve uma avaria",
                      "tivemos um acidente", "vai ser rebocada"):
            d = rules.decidir("Grupo Empresa", texto, self.cfg)
            self.assertEqual(d.categoria, rules.URGENTE, texto)

    def test_radical_nao_apanha_palavra_diferente(self):
        # "avari" não pode disparar em "avaliação" nem "aviário".
        for texto in ("preciso da avaliação do imóvel", "fomos ao aviário"):
            d = rules.decidir("Grupo", texto, self.cfg)
            self.assertNotEqual(d.categoria, rules.URGENTE, texto)

    def test_matricula_dispara(self):
        d = rules.decidir("Oficina", "o BS-76-UD está pronto", self.cfg)
        self.assertEqual(d.categoria, rules.URGENTE)
        self.assertEqual(d.motivo, "matricula")

    def test_saudacao_e_ruido(self):
        for texto in ("Bom dia", "bom dia!", "👍", "Obrigado."):
            self.assertEqual(rules.decidir("Grupo Igreja", texto, self.cfg).categoria,
                             rules.RUIDO, texto)

    def test_saudacao_com_conteudo_nao_e_ruido(self):
        # A regressão que interessa: o urgente não pode morrer atrás de um "bom dia".
        d = rules.decidir("Grupo Empresa", "Bom dia, o camião avariou em Aveiro", self.cfg)
        self.assertEqual(d.categoria, rules.URGENTE)

    def test_conversa_ignorada(self):
        d = rules.decidir("Promocoes XPTO", "50% de desconto", self.cfg)
        self.assertEqual(d.categoria, rules.RUIDO)

    def test_resto_vai_ao_modelo(self):
        d = rules.decidir("João Silva", "Tenho uma proposta de trabalho para si", self.cfg)
        self.assertEqual(d.categoria, rules.DESCONHECIDO)
        self.assertTrue(d.precisa_modelo)

    def test_media_nunca_e_urgente_mas_e_registada(self):
        d = rules.decidir("Bruno Figueiredo", "Mensagem de voz", self.cfg)
        self.assertEqual(d.triabilidade, rules.MEDIA)
        self.assertNotEqual(d.categoria, rules.DESCONHECIDO)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _guardar(self, **kw):
        base = dict(
            conversa="X", app="com.whatsapp", texto="olá", triabilidade=rules.TRIAVEL,
            categoria=rules.INFORMATIVO, motivo="teste", avisado=False,
            reter_horas=48, guardar_texto=True,
        )
        base.update(kw)
        return self.store.guardar(**base)

    def test_texto_expirado_e_apagado_mas_a_linha_fica(self):
        mid = self._guardar(reter_horas=-1)  # já expirado
        self.assertEqual(self.store.apagar_textos_expirados(), 1)
        linha = self.store.para_resumo()[0]
        self.assertEqual(linha.id, mid)
        self.assertIsNone(linha.texto)
        self.assertTrue(linha.texto_apagado)

    def test_texto_dentro_do_prazo_sobrevive(self):
        self._guardar(reter_horas=48)
        self.assertEqual(self.store.apagar_textos_expirados(), 0)
        self.assertEqual(self.store.para_resumo()[0].texto, "olá")

    def test_modo_sem_guardar_texto(self):
        self._guardar(guardar_texto=False)
        linha = self.store.para_resumo()[0]
        self.assertIsNone(linha.texto)
        self.assertTrue(linha.texto_apagado)

    def test_teto_de_chamadas(self):
        self.assertEqual(self.store.chamadas_hoje(), 0)
        self.store.consumir_chamada(20)
        self.store.consumir_chamada(15)
        self.assertEqual(self.store.chamadas_hoje(), 2)

    def test_ruido_nao_entra_no_resumo(self):
        self._guardar(categoria=rules.RUIDO)
        self.assertEqual(self.store.para_resumo(), [])
        self.assertEqual(self.store.contagem_ruido(), 1)

    def test_marcar_no_resumo_nao_repete(self):
        mid = self._guardar()
        self.store.marcar_no_resumo([mid])
        self.assertEqual(self.store.para_resumo(), [])

    def test_pulso_regista_a_ultima(self):
        self.assertIsNone(self.store.ultima_recebida())
        self._guardar()
        self.assertIsNotNone(self.store.ultima_recebida())


class TestClassificacao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.cfg = _cfg()

    def tearDown(self):
        self.tmp.cleanup()

    def _mensagens(self, n=2):
        from mia_routines.whatsapp.store import Mensagem
        return [Mensagem(i, "C", "texto", rules.DESCONHECIDO, "modelo", "", False)
                for i in range(1, n + 1)]

    def test_teto_atingido_cai_no_seguro_sem_chamar_o_modelo(self):
        from mia_routines.whatsapp import classify
        for _ in range(self.cfg.teto_chamadas_dia):
            self.store.consumir_chamada(1)
        antes = self.store.chamadas_hoje()
        resultado = classify.classificar(self._mensagens(), self.cfg, self.store)
        self.assertTrue(all(c == rules.RESPOSTA for c, _ in resultado.values()))
        # Nenhuma chamada consumida: prova que o modelo não foi contactado.
        self.assertEqual(self.store.chamadas_hoje(), antes)

    def test_json_invalido_cai_no_seguro(self):
        from mia_routines.whatsapp.classify import _interpretar
        resultado = _interpretar("desculpa, não percebi", self._mensagens())
        self.assertEqual(len(resultado), 2)
        self.assertTrue(all(c == rules.RESPOSTA for c, _ in resultado.values()))

    def test_ids_omitidos_pelo_modelo_nao_ficam_por_classificar(self):
        from mia_routines.whatsapp.classify import _interpretar
        resultado = _interpretar('[{"id": 1, "categoria": "ruido"}]', self._mensagens())
        self.assertEqual(resultado[1], (rules.RUIDO, "modelo"))
        self.assertEqual(resultado[2][0], rules.RESPOSTA)

    def test_categoria_inventada_e_rejeitada(self):
        from mia_routines.whatsapp.classify import _interpretar
        resultado = _interpretar('[{"id": 1, "categoria": "muito_urgente"}]',
                                 self._mensagens(1))
        self.assertEqual(resultado[1][0], rules.RESPOSTA)


class TestServidor(unittest.TestCase):
    """Sobe o servidor a sério: a autenticação é a parte que não pode falhar."""

    def setUp(self):
        from mia_routines.whatsapp import server

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.cfg = _cfg()
        self.enviados = []

        self.patch = mock.patch.object(
            server, "enviar", lambda t, c, texto: self.enviados.append(texto) or True
        )
        self.patch.start()

        server._Handler.cfg = self.cfg
        server._Handler.store = self.store
        server._Handler._recentes = {}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server._Handler)
        self.porta = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.patch.stop()
        self.tmp.cleanup()

    def _post(self, corpo: dict, token: str | None = TOKEN):
        pedido = urllib.request.Request(
            f"http://127.0.0.1:{self.porta}/notificacao",
            data=json.dumps(corpo).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if token is not None:
            pedido.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(pedido, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_sem_token_e_recusado(self):
        codigo, _ = self._post({"title": "X", "text": "olá"}, token=None)
        self.assertEqual(codigo, 401)

    def test_token_errado_e_recusado(self):
        codigo, _ = self._post({"title": "X", "text": "olá"}, token="y" * 40)
        self.assertEqual(codigo, 401)

    def test_nada_e_guardado_sem_autorizacao(self):
        self._post({"title": "X", "text": "olá"}, token=None)
        self.assertEqual(self.store.para_resumo(), [])

    def test_notificacao_valida_e_aceite(self):
        codigo, corpo = self._post(
            {"packageName": "com.whatsapp", "title": "João", "text": "tenho uma proposta"}
        )
        self.assertEqual(codigo, 200)
        self.assertEqual(corpo["estado"], "aceite")

    def test_urgente_avisa_de_imediato(self):
        self._post({"packageName": "com.whatsapp", "title": "Oficina",
                    "text": "houve um acidente com a carrinha"})
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("acidente", self.enviados[0])

    def test_app_que_nao_e_whatsapp_e_descartada(self):
        codigo, corpo = self._post(
            {"packageName": "com.instagram.android", "title": "X", "text": "olá"}
        )
        self.assertEqual(corpo["estado"], "ignorado")
        self.assertEqual(self.store.para_resumo(), [])

    def test_duplicado_nao_avisa_duas_vezes(self):
        for _ in range(3):
            self._post({"packageName": "com.whatsapp", "title": "Oficina",
                        "text": "acidente na A1"})
        self.assertEqual(len(self.enviados), 1)

    def test_json_invalido_devolve_400(self):
        pedido = urllib.request.Request(
            f"http://127.0.0.1:{self.porta}/notificacao",
            data=b"nao sou json",
            headers={"Authorization": f"Bearer {TOKEN}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(pedido, timeout=5)
            self.fail("devia ter falhado")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_saude_responde(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.porta}/saude", timeout=5) as r:
            self.assertEqual(json.loads(r.read())["estado"], "vivo")


class TestResumo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.cfg = _cfg()

    def tearDown(self):
        self.tmp.cleanup()

    def _guardar(self, **kw):
        base = dict(conversa="Grupo", app="com.whatsapp", texto="olá",
                    triabilidade=rules.TRIAVEL, categoria=rules.RESPOSTA,
                    motivo="modelo", avisado=False, reter_horas=48, guardar_texto=True)
        base.update(kw)
        return self.store.guardar(**base)

    def test_resumo_cita_o_original(self):
        from mia_routines.whatsapp.digest import construir_resumo
        self._guardar(texto="Tem disponibilidade para uma rota extra?")
        texto, ids = construir_resumo(self.cfg, self.store)
        self.assertIn("Tem disponibilidade para uma rota extra?", texto)
        self.assertEqual(len(ids), 1)

    def test_resumo_diz_quando_o_texto_nao_existe(self):
        from mia_routines.whatsapp.digest import construir_resumo
        self._guardar(guardar_texto=False)
        texto, _ = construir_resumo(self.cfg, self.store)
        self.assertIn("não guardado", texto)

    def test_resumo_escapa_html(self):
        from mia_routines.whatsapp.digest import construir_resumo
        self._guardar(texto="<script>alert(1)</script>")
        texto, _ = construir_resumo(self.cfg, self.store)
        self.assertNotIn("<script>", texto)

    def test_resumo_vazio_nao_inventa(self):
        from mia_routines.whatsapp.digest import construir_resumo
        texto, ids = construir_resumo(self.cfg, self.store)
        self.assertEqual(texto, "")
        self.assertEqual(ids, [])

    def test_pulso_avisa_apos_silencio(self):
        from mia_routines.whatsapp import digest
        self._guardar()
        antiga = agora() - timedelta(hours=10)
        with self.store._conn() as conn:
            conn.execute("UPDATE pulso SET ultima_utc = ? WHERE id = 1", (_iso(antiga),))
        avisos = []
        with mock.patch.object(digest, "enviar",
                               lambda t, c, txt: avisos.append(txt) or True), \
             mock.patch.object(digest, "agora", lambda: antiga + timedelta(hours=10)):
            cfg = _cfg(pulso_inicio=0, pulso_fim=24)
            digest.verificar_pulso(cfg, self.store)
        self.assertEqual(len(avisos), 1)
        self.assertIn("Não recebo nada", avisos[0])

    def test_pulso_nao_repete_o_aviso(self):
        from mia_routines.whatsapp import digest
        self._guardar()
        antiga = agora() - timedelta(hours=10)
        with self.store._conn() as conn:
            conn.execute("UPDATE pulso SET ultima_utc = ? WHERE id = 1", (_iso(antiga),))
        avisos = []
        cfg = _cfg(pulso_inicio=0, pulso_fim=24)
        with mock.patch.object(digest, "enviar",
                               lambda t, c, txt: avisos.append(txt) or True):
            digest.verificar_pulso(cfg, self.store)
            digest.verificar_pulso(cfg, self.store)
        self.assertEqual(len(avisos), 1)


class TestConfig(unittest.TestCase):
    def test_token_curto_e_recusado(self):
        ambiente = {"TRIAGEM_INGEST_TOKEN": "curto", "TELEGRAM_BOT_TOKEN": "t",
                    "TELEGRAM_CHAT_ID": "1"}
        with mock.patch.dict(os.environ, ambiente, clear=True):
            with self.assertRaises(RuntimeError):
                TriageConfig.from_env()

    def test_falta_telegram_e_recusado(self):
        with mock.patch.dict(os.environ, {"TRIAGEM_INGEST_TOKEN": "x" * 40}, clear=True):
            with self.assertRaises(RuntimeError):
                TriageConfig.from_env()


if __name__ == "__main__":
    unittest.main()
