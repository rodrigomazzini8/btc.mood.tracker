# -*- coding: utf-8 -*-
"""
Testes do `cycle_model.py` — o modelo de ciclo 0–100.

Sem rede: tudo roda com séries sintéticas e com `usar_coinmetrics=False`.
O que garantimos aqui:
  - integridade das escalas (o erro mais fácil de cometer ao recalibrar);
  - direção do modelo (topo pontua mais que fundo);
  - o peso de um pilar sem dado ser redistribuído, sem tirar o score de 0–100;
  - o histórico não olhar o futuro;
  - as curvas de posição andarem na direção certa.
"""

import numpy as np
import pandas as pd
import pytest

import cycle_model as cm


# ---------------------------------------------------------------- escalas

def test_escalas_tem_valores_crescentes():
    """np.interp exige x crescente — escala fora de ordem devolve lixo."""
    for chave, pontos in cm.ESCALAS.items():
        xs = [p[0] for p in pontos]
        assert xs == sorted(xs), f"escala '{chave}' com valores fora de ordem"


def test_escalas_sao_monotonicas_no_score():
    """Mais caro = score maior. A exceção proposital é o relógio do halving."""
    for chave, pontos in cm.ESCALAS.items():
        if chave == "halving":
            continue
        ys = [p[1] for p in pontos]
        assert ys == sorted(ys), f"escala '{chave}' não é monotônica"


def test_escalas_ficam_entre_0_e_100():
    for chave, pontos in cm.ESCALAS.items():
        for _valor, score in pontos:
            assert 0 <= score <= 100, f"escala '{chave}' fora de 0..100"


def test_toda_fonte_de_pilar_aponta_para_escala_existente():
    for pilar in cm.PILARES:
        for _serie, escala, rotulo, _tipo in pilar["fontes"]:
            assert escala in cm.ESCALAS, f"{rotulo}: escala '{escala}' não existe"


def test_pesos_dos_pilares_somam_um():
    assert sum(p["peso"] for p in cm.PILARES) == pytest.approx(1.0)


def test_normalizar_trava_nos_extremos():
    assert cm.normalizar("mvrv_z", -99) == 0
    assert cm.normalizar("mvrv_z", 99) == 100


def test_normalizar_valor_ausente_vira_nan():
    for ausente in (None, float("nan"), "", "abc"):
        assert np.isnan(cm.normalizar("nupl", ausente))


def test_escalas_calibradas_marcam_os_topos_recentes():
    """
    Regressão da calibração: os topos de 2024/2025 tiveram MVRV Z-Score de
    2,9 e 2,5. A escala precisa lê-los como zona de topo (>=70), senão
    voltamos ao bug de exigir "Z > 6" e nunca marcar topo.
    """
    assert cm.normalizar("mvrv_z", 2.93) >= 70
    assert cm.normalizar("mvrv_z", 2.53) >= 65
    # e os fundos reais precisam ficar em zona de fundo
    assert cm.normalizar("mvrv_z", -0.49) <= 10
    assert cm.normalizar("nupl", -0.29) <= 10


# ------------------------------------------------------------------ fases

@pytest.mark.parametrize("score,fase", [
    (0, "FUNDO PROFUNDO"), (14.9, "FUNDO PROFUNDO"), (15, "ACUMULAÇÃO"),
    (34.9, "ACUMULAÇÃO"), (35, "EXPANSÃO"), (59.9, "EXPANSÃO"),
    (60, "DISTRIBUIÇÃO"), (79.9, "DISTRIBUIÇÃO"), (80, "EUFORIA"), (100, "EUFORIA"),
])
def test_fase_do_score(score, fase):
    assert cm.fase_do_score(score) == fase


def test_fase_de_score_invalido():
    assert cm.fase_do_score(float("nan")) == "—"


# ------------------------------------------------------------- o modelo

def test_snapshot_sem_rede(preco_sintetico):
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      usar_coinmetrics=False)
    assert 0 <= res["score"] <= 100
    assert res["fase"] in [f[1] for f in cm.FASES]
    assert len(res["componentes"]) == len(cm.PILARES)
    assert res["cobertura"] > 60  # sem chave nenhuma ainda cobrimos o grosso


def test_preco_curto_demais_levanta_erro():
    curto = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10),
                          "price": np.linspace(100, 110, 10)})
    with pytest.raises(ValueError):
        cm.calcular(curto, usar_coinmetrics=False)


def test_historico_fica_na_faixa(preco_sintetico):
    hist = cm.serie_score(preco_sintetico, series_onchain={})
    assert len(hist) > 500
    assert hist["score"].between(0, 100).all()
    assert list(hist.columns[:3]) == ["date", "price", "score"]


def test_topo_pontua_mais_que_fundo(preco_sintetico):
    hist = cm.serie_score(preco_sintetico, series_onchain={})
    topo = hist.loc[hist["price"].idxmax()]
    depois = hist[hist["date"] > hist["date"].min() + pd.Timedelta(days=500)]
    fundo = hist.loc[depois["price"].idxmin()]
    assert topo["score"] > fundo["score"] + 20


def test_historico_nao_olha_o_futuro(preco_sintetico):
    """
    Cortar a série no meio não pode mudar o score dos dias anteriores ao
    corte — se mudar, alguma métrica está usando dados do futuro.
    """
    completo = cm.serie_score(preco_sintetico, series_onchain={}).set_index("date")
    corte = preco_sintetico["date"].iloc[-400]
    parcial = cm.serie_score(preco_sintetico[preco_sintetico["date"] <= corte],
                             series_onchain={}).set_index("date")
    comum = parcial.index[-200:]
    assert np.allclose(completo.loc[comum, "score"], parcial.loc[comum, "score"])


def test_pilar_sem_dado_redistribui_peso(preco_sintetico):
    """Sem Fear & Greed o modelo continua na escala 0–100, só com menos peso."""
    com_fng = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                          usar_coinmetrics=False)
    sem_fng = cm.calcular(preco_sintetico, series_onchain={},
                          usar_coinmetrics=False)
    assert 0 <= sem_fng["score"] <= 100
    assert sem_fng["cobertura"] == pytest.approx(com_fng["cobertura"])


def test_onchain_tem_prioridade_sobre_proxy(preco_sintetico):
    """Com MVRV Z-Score de verdade, o pilar não pode cair no proxy de preço."""
    datas = preco_sintetico["date"]
    oc = {"mvrv_z": pd.DataFrame({"date": datas,
                                  "valor": np.linspace(-0.5, 6.0, len(datas))})}
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain=oc,
                      usar_coinmetrics=False)
    valuation = next(c for c in res["componentes"] if c["chave"] == "valuation")
    assert valuation["tipo"] == "on-chain"
    assert res["fonte"] == "on-chain"


# --------------------------------------------------------- posição/plano

def test_exposicao_cai_conforme_o_ciclo_esquenta():
    valores = [cm.alvo_exposicao(s) for s in range(0, 101, 10)]
    assert valores == sorted(valores, reverse=True)
    assert valores[0] > 90 and valores[-1] < 20


def test_dca_compra_mais_no_fundo():
    assert cm.multiplicador_dca(5) > cm.multiplicador_dca(50) > cm.multiplicador_dca(85)
    assert cm.multiplicador_dca(95) == 0


def test_plano_bate_com_a_fase():
    assert cm.plano_posicao(5)["acao"] == "ACUMULAR AGRESSIVO"
    assert cm.plano_posicao(90)["acao"] == "REALIZAR / CAIXA"
    plano = cm.plano_posicao(50)
    assert plano["exposicao_alvo"] + plano["realizado_alvo"] == pytest.approx(100)


def test_plano_com_score_invalido():
    plano = cm.plano_posicao(float("nan"))
    assert plano["acao"] == "—"


# ---------------------------------------------------------- semanal/backtest

def test_serie_semanal_nao_passa_da_ultima_data(preco_sintetico):
    hist = cm.serie_score(preco_sintetico, series_onchain={})
    sem = cm.serie_semanal(hist)
    assert not sem.empty
    assert sem["date"].max() <= hist["date"].max()
    assert set(sem["fase"]).issubset({f[1] for f in cm.FASES})


def test_backtest_reduz_drawdown(preco_sintetico):
    hist = cm.serie_score(preco_sintetico, series_onchain={})
    bt = cm.backtest_exposicao(hist)
    assert bt["modelo"]["dd_max"] >= bt["hold"]["dd_max"]
    assert 0 <= bt["modelo"]["exposicao_media"] <= 100


def test_backtest_sem_historico_suficiente():
    curto = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=30),
                          "price": np.linspace(1, 2, 30),
                          "score": np.linspace(10, 90, 30)})
    assert cm.backtest_exposicao(curto) == {}


# ------------------------------------------------------------------ card

def test_card_html_tem_score_e_svg(preco_sintetico):
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      usar_coinmetrics=False)
    html = cm.card_html(res)
    assert "<svg" in html and "bcm-card" in html
    assert str(int(round(res["score"]))) in html
    assert res["fase"] in html


def test_card_escapa_texto_perigoso(preco_sintetico):
    """O card monta HTML na mão: rótulo com '<' não pode virar tag."""
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      usar_coinmetrics=False)
    res["componentes"][0]["nome"] = "<script>alert(1)</script>"
    html = cm.card_html(res)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
