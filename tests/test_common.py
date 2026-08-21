# -*- coding: utf-8 -*-
"""
Testes do `common.py` — a camada de dados.

As funções de rede são testadas com `requests.get` trocado por um dublê:
conferimos o PARSING (que é onde os bugs moram) e, principalmente, que uma
falha de rede devolve DataFrame vazio em vez de derrubar o app.
"""

import numpy as np
import pandas as pd
import pytest

import common


class RespostaFalsa:
    """Dublê mínimo de `requests.Response`."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ------------------------------------------------------------ estatística

def test_desvio_da_media_soma_zero():
    serie = pd.Series([10.0, 20, 30, 40])
    desvio = common.desvio_da_media(serie)
    assert desvio.sum() == pytest.approx(0.0)


def test_correlacao_perfeita():
    a = pd.Series(np.arange(50, dtype=float))
    assert common.correlacao(a, a * 3 + 1) == pytest.approx(1.0)
    assert common.correlacao(a, -a) == pytest.approx(-1.0)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_correlacao_com_serie_constante_nao_quebra():
    a = pd.Series(np.arange(30, dtype=float))
    resultado = common.correlacao(a, pd.Series([5.0] * 30))
    assert resultado != resultado or resultado == 0  # NaN ou zero, mas sem erro


def test_correlacao_defasada_encontra_a_defasagem():
    """
    Humor que ANTECIPA o preço em 3 dias: a maior correlação tem de aparecer
    justamente na defasagem de 3.
    """
    n = 200
    base = np.sin(np.arange(n) / 8.0)
    preco = pd.Series(base)
    humor = pd.Series(np.roll(base, -3))  # humor vem 3 dias antes
    tabela = common.correlacao_defasada(preco, humor, max_lag=6)
    melhor = tabela.loc[tabela["corr"].idxmax(), "lag"]
    assert int(melhor) == 3


# ------------------------------------------------------------- fear&greed

def test_fear_greed_parseia_payload(monkeypatch):
    payload = {"data": [
        {"timestamp": "1700000000", "value": "72", "value_classification": "Greed"},
        {"timestamp": "1699913600", "value": "35", "value_classification": "Fear"},
    ]}
    monkeypatch.setattr(common.requests, "get",
                        lambda *a, **k: RespostaFalsa(payload))
    df = common.fetch_fear_greed(limit=2)
    assert list(df.columns) == ["date", "fng"]
    assert len(df) == 2
    assert df["date"].is_monotonic_increasing      # ordenado do mais antigo
    assert df["fng"].tolist() == [35.0, 72.0]
    assert str(df["date"].dt.time.iloc[0]) == "00:00:00"   # normalizado


def test_fear_greed_com_falha_de_rede(monkeypatch):
    def _explode(*a, **k):
        raise OSError("sem rede")

    monkeypatch.setattr(common.requests, "get", _explode)
    df = common.fetch_fear_greed()
    assert df.empty
    assert list(df.columns) == ["date", "fng"]


def test_fear_greed_com_resposta_vazia(monkeypatch):
    monkeypatch.setattr(common.requests, "get",
                        lambda *a, **k: RespostaFalsa({"data": []}))
    assert common.fetch_fear_greed().empty


# ------------------------------------------------------------------ preço

def test_cryptocompare_parseia_e_ordena(monkeypatch):
    payload = {"Response": "Success", "Data": {"Data": [
        {"time": 1699913600, "close": 36000.0},
        {"time": 1700000000, "close": 37000.0},
        {"time": 1700086400, "close": 0},        # dia sem preço: deve sair
    ]}}
    monkeypatch.setattr(common.requests, "get",
                        lambda *a, **k: RespostaFalsa(payload))
    df = common.fetch_btc_cryptocompare(dias=3)
    assert list(df.columns) == ["date", "price"]
    assert (df["price"] > 0).all()
    assert df["date"].is_monotonic_increasing


def test_cascata_de_preco_usa_o_proximo_quando_o_primeiro_falha(monkeypatch):
    """
    `fetch_btc_price` tenta Binance -> CryptoCompare -> CoinGecko. Se a
    primeira devolver vazio, a segunda tem de ser usada (é o que salva o app
    em datacenter, onde a Binance responde 451).
    """
    esperado = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=5),
                             "price": [1.0, 2, 3, 4, 5]})
    monkeypatch.setattr(common, "fetch_btc_binance",
                        lambda *a, **k: pd.DataFrame(columns=["date", "price"]))
    monkeypatch.setattr(common, "fetch_btc_cryptocompare", lambda *a, **k: esperado)
    df = common.fetch_btc_price(dias=5)
    assert len(df) == 5
    assert df["price"].tolist() == [1.0, 2, 3, 4, 5]


def test_cascata_devolve_vazio_se_todas_falharem(monkeypatch):
    vazio = pd.DataFrame(columns=["date", "price"])
    for nome in ("fetch_btc_binance", "fetch_btc_cryptocompare", "fetch_btc_coingecko"):
        monkeypatch.setattr(common, nome, lambda *a, **k: vazio)
    assert common.fetch_btc_price(dias=5).empty
