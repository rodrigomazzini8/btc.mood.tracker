# -*- coding: utf-8 -*-
"""
Testes do `termometro.py` — o score consolidado de −2 a +2.

Sem rede: `usar_coinmetrics=False` em tudo que buscaria dados.
"""

import numpy as np
import pandas as pd
import pytest

import termometro as term


# ------------------------------------------------------------------ faixas

def test_faixas_tem_limiares_crescentes():
    for chave, faixas in term.FAIXAS.items():
        limiares = [f[0] for f in faixas]
        assert limiares == sorted(limiares), f"faixa '{chave}' fora de ordem"
        assert limiares[-1] == float("inf"), f"faixa '{chave}' sem 'resto'"


def test_faixas_dao_scores_de_2_a_menos_2():
    for chave, faixas in term.FAIXAS.items():
        scores = [f[1] for f in faixas]
        assert scores == [2, 1, 0, -1, -2], f"faixa '{chave}' com scores estranhos"


def test_todo_indicador_tem_nome_e_explicacao():
    for chave in term.FAIXAS:
        assert chave in term.NOMES
        assert term.EXPLICACOES.get(chave)


def test_score_por_faixa_usa_o_limiar_superior():
    faixas = [(1.0, 2), (2.0, 1), (3.0, 0), (4.0, -1), (float("inf"), -2)]
    assert term._score_por_faixas(0.5, faixas) == 2
    assert term._score_por_faixas(1.0, faixas) == 2   # limiar é inclusivo
    assert term._score_por_faixas(1.01, faixas) == 1
    assert term._score_por_faixas(99, faixas) == -2


def test_indicador_indisponivel_vira_neutro():
    assert term._score_por_faixas(float("nan"), term.FAIXAS["mvrv"]) == 0
    assert term._score_por_faixas(None, term.FAIXAS["mvrv"]) == 0


def test_faixas_recalibradas_marcam_os_topos_recentes():
    """
    Regressão da calibração: com as faixas antigas o topo de out/2025
    (MVRV 2.29, MVRV-Z 2.53, NUPL 0.56) saía como NEUTRO.
    """
    assert term.score_indicador("mvrv", 2.29) <= -1
    assert term.score_indicador("mvrv_z", 2.53) <= -1
    assert term.score_indicador("nupl", 0.56) <= -1
    # e os fundos reais continuam como compra
    assert term.score_indicador("mvrv", 0.78) == 2
    assert term.score_indicador("rsi_mensal", 34.5) == 2


@pytest.mark.parametrize("score,sinal", [
    (2, "COMPRA FORTE"), (1.5, "COMPRA FORTE"), (1.0, "COMPRA"), (0.5, "COMPRA"),
    (0.0, "NEUTRO"), (-0.4, "NEUTRO"), (-1.0, "VENDA"), (-1.6, "VENDA FORTE"),
])
def test_score_para_sinal(score, sinal):
    assert term.score_para_sinal(score) == sinal


# --------------------------------------------------------------- snapshot

@pytest.fixture(scope="module")
def snapshot(preco_sintetico):
    return term.montar_snapshot(preco_sintetico, fng_atual=42.0,
                                usar_coinmetrics=False)


def test_snapshot_traz_os_indicadores_gratis(snapshot):
    gratis = snapshot[~snapshot["onchain"]]
    assert set(gratis["chave"]) == {"mayer", "ma200w", "rsi_mensal", "fng"}
    assert gratis["ok"].all()


def test_snapshot_sem_chave_nao_inventa_onchain(snapshot):
    onchain = snapshot[snapshot["onchain"]]
    assert onchain.empty or not onchain["ok"].any()


def test_consolidado_ignora_indisponivel(snapshot):
    consolidado = term.consolidar(snapshot)
    assert -2 <= consolidado <= 2


def test_consolidado_respeita_pesos(snapshot):
    """Peso zero num indicador tem de mudar (ou manter) o consolidado sem quebrar."""
    chaves = list(snapshot[snapshot["ok"]]["chave"])
    pesos = {c: 0.0 for c in chaves}
    pesos[chaves[0]] = 1.0
    so_um = term.consolidar(snapshot, pesos=pesos)
    esperado = snapshot.loc[snapshot["chave"] == chaves[0], "score"].iloc[0]
    assert so_um == pytest.approx(esperado)


def test_consolidado_de_selecao_vazia_e_nan(snapshot):
    assert np.isnan(term.consolidar(snapshot, selecionados=[]))


# --------------------------------------------------------- séries do preço

def test_mayer_e_preco_sobre_media_200(preco_sintetico):
    mayer = term.serie_mayer_multiple(preco_sintetico).dropna()
    s = preco_sintetico.set_index("date")["price"]
    esperado = s.iloc[-1] / s.tail(200).mean()
    assert mayer.iloc[-1] == pytest.approx(esperado, rel=1e-6)


def test_rsi_mensal_fica_entre_0_e_100(preco_sintetico):
    rsi = term.serie_rsi_mensal(preco_sintetico).dropna()
    assert not rsi.empty
    assert rsi.between(0, 100).all()


# ---------------------------------------------------------------- backtest

def test_backtest_do_score(preco_sintetico):
    fng = pd.DataFrame({"date": preco_sintetico["date"],
                        "fng": np.linspace(20, 80, len(preco_sintetico))})
    hist = term.serie_score_historico(preco_sintetico, fng,
                                      usar_coinmetrics=False)
    assert not hist.empty
    assert hist["score"].between(-2, 2).all()

    bt = term.backtest_score(hist)
    assert bt["estrategia"]["exposicao"] <= 1.0
    assert bt["estrategia"]["dd_max"] >= bt["hold"]["dd_max"]
    assert 0 <= bt["estrategia"]["win_rate"] <= 1


def test_backtest_sem_dados_devolve_vazio():
    assert term.backtest_score(pd.DataFrame()) == {}
