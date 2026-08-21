# -*- coding: utf-8 -*-
"""
Testes do bot do Telegram.

Sem rede e sem token: a camada HTTP é substituída por um dublê, e os dados
de mercado vêm de um contexto falso. O que interessa aqui é o roteamento de
comandos, a formatação das mensagens e as regras de assinatura — a parte que
erra silenciosamente em produção.
"""

import json

import pandas as pd
import pytest

import cycle_model as cm
import telegram_bot as tg


# --------------------------------------------------------------- fixtures

class ContextoFalso:
    """Mesma interface do Contexto real, com dados fixos."""

    def __init__(self, preco, explodir=False):
        self._preco = preco
        self._explodir = explodir

    def dados(self, forcar=False):
        if self._explodir:
            raise RuntimeError("fonte fora do ar")
        snap = cm.calcular(self._preco, fng_atual=50.0, series_onchain={},
                           usar_coinmetrics=False)
        hist = cm.serie_score(self._preco, series_onchain={})
        fng = pd.DataFrame({"date": [self._preco["date"].iloc[-1]], "fng": [50.0]})
        return {"preco": self._preco, "fng": fng, "cm": pd.DataFrame(),
                "oc": {}, "snap": snap, "hist": hist,
                "semanal": cm.serie_semanal(hist)}


@pytest.fixture(scope="module")
def contexto(preco_sintetico):
    return ContextoFalso(preco_sintetico)


@pytest.fixture
def enviadas(monkeypatch):
    """Captura o que seria enviado ao Telegram."""
    caixa = []

    def _falso(metodo, dados, token=None, timeout=None):
        caixa.append((metodo, dados))
        return {"ok": True, "result": {}}

    monkeypatch.setattr(tg, "_chamar", _falso)
    return caixa


# ------------------------------------------------------------- roteamento

def test_mensagem_que_nao_e_comando_fica_calada(contexto):
    """Em grupo, o bot não pode responder a toda conversa."""
    assert tg.responder("bom dia pessoal", contexto, chat_id="1") is None
    assert tg.responder("", contexto, chat_id="1") is None


def test_ajuda_lista_os_comandos(contexto):
    for cmd in ("/start", "/ajuda", "/help"):
        resposta = tg.responder(cmd, contexto, chat_id="1")
        assert "/score" in resposta and "/rebalancear" in resposta


def test_comando_com_arroba_do_grupo(contexto):
    """Em grupos o Telegram entrega /score@MeuBot."""
    resposta = tg.responder("/score@MeuBotBTC", contexto, chat_id="1")
    assert "score" in resposta.lower()


def test_comando_desconhecido_ajuda(contexto):
    resposta = tg.responder("/tchibum", contexto, chat_id="1")
    assert "Não conheço esse comando" in resposta


def test_score_traz_fase_acao_e_composicao(contexto):
    resposta = tg.responder("/score", contexto, chat_id="1")
    assert "/100" in resposta
    assert "Composição" in resposta
    assert "MVRV Z-Score" in resposta
    assert "não é recomendação financeira" in resposta


def test_preco_e_semanal(contexto):
    assert "BTC $" in tg.responder("/preco", contexto, chat_id="1")
    semanal = tg.responder("/semanal", contexto, chat_id="1")
    assert "Score semanal" in semanal


def test_termometro(contexto):
    resposta = tg.responder("/termometro", contexto, chat_id="1")
    assert "indicadores" in resposta


def test_falha_de_dados_vira_mensagem_e_nao_excecao(preco_sintetico):
    ruim = ContextoFalso(preco_sintetico, explodir=True)
    resposta = tg.responder("/score", ruim, chat_id="1")
    assert "Não consegui buscar os dados" in resposta


# ---------------------------------------------------------- rebalanceamento

def test_rebalancear_pede_os_numeros(contexto):
    resposta = tg.responder("/rebalancear", contexto, chat_id="1")
    assert "Use assim" in resposta


def test_rebalancear_calcula(contexto):
    resposta = tg.responder("/rebalancear 10000 1000", contexto, chat_id="1")
    assert "%" in resposta and ("COMPRAR" in resposta or "VENDER" in resposta
                                or "MANTER" in resposta)


def test_rebalancear_aceita_virgula(contexto):
    resposta = tg.responder("/rebalancear 10000,50 2000,25", contexto, chat_id="1")
    assert "alvo" in resposta


def test_rebalancear_com_patrimonio_zero(contexto):
    resposta = tg.responder("/rebalancear 0 0", contexto, chat_id="1")
    assert "inválidos" in resposta


# --------------------------------------------------------------- assinantes

def test_assinar_e_desassinar(tmp_path, contexto):
    arq = str(tmp_path / "subs.json")
    assert tg.responder("/alertas", contexto, chat_id="7", arquivo_assinantes=arq)
    r1 = tg.responder("/alertas on", contexto, chat_id="7", arquivo_assinantes=arq)
    assert "aviso aqui" in r1
    assert tg.ler_assinantes(arq) == ["7"]

    r2 = tg.responder("/alertas on", contexto, chat_id="7", arquivo_assinantes=arq)
    assert "já recebe" in r2                      # idempotente

    r3 = tg.responder("/alertas off", contexto, chat_id="7", arquivo_assinantes=arq)
    assert "desligados" in r3
    assert tg.ler_assinantes(arq) == []

    r4 = tg.responder("/alertas off", contexto, chat_id="7", arquivo_assinantes=arq)
    assert "não estava" in r4


def test_assinantes_com_arquivo_corrompido(tmp_path):
    arq = tmp_path / "subs.json"
    arq.write_text("{isso não é json")
    assert tg.ler_assinantes(str(arq)) == []      # degrada, não quebra


def test_alertas_nao_precisa_de_dados(preco_sintetico, tmp_path):
    """/alertas tem de funcionar mesmo com as fontes fora do ar."""
    ruim = ContextoFalso(preco_sintetico, explodir=True)
    resposta = tg.responder("/alertas on", ruim, chat_id="9",
                            arquivo_assinantes=str(tmp_path / "s.json"))
    assert "aviso aqui" in resposta


# ------------------------------------------------------------------ envio

def test_enviar_usa_html_e_sem_preview(enviadas):
    assert tg.enviar("42", "<b>oi</b>", token="t")
    metodo, dados = enviadas[0]
    assert metodo == "sendMessage"
    assert dados["chat_id"] == "42"
    assert dados["parse_mode"] == "HTML"
    assert dados["disable_web_page_preview"] is True


def test_envio_para_todos_junta_assinantes_e_chat_fixo(monkeypatch, enviadas):
    monkeypatch.setenv(tg.CHAT_ENV, "999")
    n = tg.enviar_para_todos("oi", chats=["1", "2", "999"], token="t")
    destinos = {d["chat_id"] for _, d in enviadas}
    assert n == 3 and destinos == {"1", "2", "999"}


def test_envio_sem_token_nao_levanta(monkeypatch):
    monkeypatch.delenv(tg.TOKEN_ENV, raising=False)
    assert tg.enviar("1", "oi") is False


def test_falha_da_api_nao_levanta(monkeypatch):
    def _explode(*a, **k):
        raise OSError("sem rede")
    monkeypatch.setattr("requests.post", _explode)
    assert tg.enviar("1", "oi", token="t") is False
    assert tg.buscar_updates(token="t") == []


# ------------------------------------------------------------- polling

def test_polling_responde_e_avanca_offset(monkeypatch, contexto):
    updates = [{"update_id": 10,
                "message": {"chat": {"id": 55}, "text": "/preco"}}]
    vistos = {}

    def _updates(offset=0, espera=25, token=None):
        vistos["offset"] = offset
        return updates if offset == 0 else []

    respostas = []
    monkeypatch.setattr(tg, "buscar_updates", _updates)
    monkeypatch.setattr(tg, "enviar",
                        lambda chat, texto, token=None: respostas.append((chat, texto)) or True)
    tg.rodar_polling(contexto, token="t", max_ciclos=2)

    assert len(respostas) == 1 and respostas[0][0] == "55"
    assert vistos["offset"] == 11          # offset avançou: não repete update


def test_polling_respeita_allowlist(monkeypatch, contexto):
    monkeypatch.setenv(tg.PERMITIDOS_ENV, "77")
    updates = [{"update_id": 1, "message": {"chat": {"id": 55}, "text": "/preco"}},
               {"update_id": 2, "message": {"chat": {"id": 77}, "text": "/preco"}}]
    respostas = []
    monkeypatch.setattr(tg, "buscar_updates",
                        lambda offset=0, espera=25, token=None: updates if offset == 0 else [])
    monkeypatch.setattr(tg, "enviar",
                        lambda chat, texto, token=None: respostas.append(chat) or True)
    tg.rodar_polling(contexto, token="t", max_ciclos=2)
    assert respostas == ["77"]


def test_polling_sobrevive_a_update_estranho(monkeypatch, contexto):
    """Update sem chat/texto não pode derrubar o loop."""
    updates = [{"update_id": 1, "message": {}},
               {"update_id": 2},
               {"update_id": 3, "message": {"chat": {"id": 5}, "text": "/preco"}}]
    respostas = []
    monkeypatch.setattr(tg, "buscar_updates",
                        lambda offset=0, espera=25, token=None: updates if offset == 0 else [])
    monkeypatch.setattr(tg, "enviar",
                        lambda chat, texto, token=None: respostas.append(chat) or True)
    assert tg.rodar_polling(contexto, token="t", max_ciclos=2) == 0
    assert respostas == ["5"]


def test_polling_sem_token_sai_com_erro(monkeypatch, contexto):
    monkeypatch.delenv(tg.TOKEN_ENV, raising=False)
    assert tg.rodar_polling(contexto, max_ciclos=1) == 1


# --------------------------------------------------------------- segurança

def test_texto_do_usuario_nao_vira_html(contexto):
    """
    O bot monta HTML na mão. Um rótulo com '<' não pode virar tag — vale
    para qualquer texto que venha de fora.
    """
    assert tg._e("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_estado_da_fase_ida_e_volta(tmp_path):
    arq = str(tmp_path / "estado.json")
    assert cm.ler_estado_fase(arq) == {}
    cm.salvar_estado_fase("ACUMULAÇÃO", 28.2, arq, extra={"preco": 73070.93})
    lido = cm.ler_estado_fase(arq)
    assert lido["fase"] == "ACUMULAÇÃO" and lido["score"] == 28.2
    assert lido["preco"] == 73070.93
    assert json.loads(open(arq, encoding="utf-8").read())["em"]
