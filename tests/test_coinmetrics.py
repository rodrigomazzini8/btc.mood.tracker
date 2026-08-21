# -*- coding: utf-8 -*-
"""
Testes do `coinmetrics.py` — a matemática das métricas derivadas.

Sem rede: montamos um CSV pequeno no formato exato do dataset da Coin
Metrics e conferimos MVRV, MVRV Z-Score, NUPL e Puell contra a conta feita
na mão.
"""

import io

import numpy as np
import pandas as pd
import pytest

import coinmetrics


def _csv_falso(dias: int = 800) -> io.StringIO:
    """CSV no formato da Coin Metrics, com números fáceis de conferir."""
    datas = pd.date_range("2020-01-01", periods=dias, freq="D")
    preco = np.linspace(10_000, 60_000, dias)
    oferta = 18_000_000 + np.arange(dias) * 900
    market_cap = preco * oferta
    mvrv = np.linspace(1.0, 3.0, dias)
    emissao = np.full(dias, 900.0) * preco
    df = pd.DataFrame({
        "time": datas, "PriceUSD": preco, "CapMrktCurUSD": market_cap,
        "CapMVRVCur": mvrv, "IssTotUSD": emissao,
    })
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


@pytest.fixture(scope="module")
def derivadas() -> pd.DataFrame:
    return coinmetrics._cm_do_csv(_csv_falso())


def test_colunas_esperadas(derivadas):
    assert list(derivadas.columns) == [
        "date", "price", "cm_mvrv", "cm_mvrv_z", "cm_nupl", "cm_puell"]


def test_nupl_vem_do_mvrv(derivadas):
    # NUPL = 1 − 1/MVRV, por definição.
    esperado = 1 - 1 / derivadas["cm_mvrv"]
    assert np.allclose(derivadas["cm_nupl"], esperado)


def test_nupl_zero_quando_mvrv_um(derivadas):
    # MVRV = 1 significa mercado exatamente no custo médio: NUPL = 0.
    linha = derivadas.iloc[0]
    assert linha["cm_mvrv"] == pytest.approx(1.0)
    assert linha["cm_nupl"] == pytest.approx(0.0, abs=1e-9)


def test_mvrv_z_cresce_com_o_mvrv(derivadas):
    z = derivadas["cm_mvrv_z"].dropna()
    assert len(z) > 100
    assert z.iloc[-1] > z.iloc[0]


def test_mvrv_z_nao_olha_o_futuro(derivadas):
    """
    O z-score usa desvio-padrão EXPANDIDO (só o passado). Truncar a série no
    meio não pode mudar os valores já calculados até ali.
    """
    # Série mais longa aqui: o z-score só começa depois de 365 dias.
    longo = pd.read_csv(_csv_falso(1200))
    completo = coinmetrics._cm_do_csv(
        io.StringIO(longo.to_csv(index=False))).set_index("date")["cm_mvrv_z"]
    truncado = coinmetrics._cm_do_csv(
        io.StringIO(longo.iloc[:900].to_csv(index=False))
    ).set_index("date")["cm_mvrv_z"]
    comum = truncado.dropna().index
    assert len(comum) > 50
    assert np.allclose(completo.loc[comum], truncado.loc[comum])


def test_puell_medio_perto_de_um(derivadas):
    # Puell = emissão / média de 365d da emissão. Com emissão crescendo devagar,
    # ele fica um pouco acima de 1 — nunca negativo, nunca absurdo.
    puell = derivadas["cm_puell"].dropna()
    assert len(puell) > 100
    assert puell.between(0.5, 3.0).all()


def test_falha_de_rede_nao_quebra(monkeypatch, tmp_path):
    """Sem rede e sem cache, a função devolve DataFrame vazio (não levanta)."""
    monkeypatch.setattr(coinmetrics, "CM_CACHE", str(tmp_path / "nao_existe.csv"))

    def _explode(*a, **k):
        raise OSError("sem rede")

    monkeypatch.setattr("requests.get", _explode)
    vazio = coinmetrics.fetch_coinmetrics()
    assert vazio.empty
    assert "cm_mvrv" in vazio.columns
