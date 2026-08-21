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


# ------------------------------------------------------- rebalanceamento

def test_rebalanceamento_manda_comprar_quando_esta_abaixo_do_alvo():
    reb = cm.plano_rebalanceamento(20, patrimonio=10_000, valor_em_btc=2_000)
    assert reb["acao"] == "COMPRAR"
    assert reb["ajuste"] > 0
    # comprar o ajuste leva exatamente ao alvo
    novo = (2_000 + reb["ajuste"]) / 10_000 * 100
    assert novo == pytest.approx(reb["alvo_pct"])


def test_rebalanceamento_manda_vender_quando_esta_acima():
    reb = cm.plano_rebalanceamento(90, patrimonio=10_000, valor_em_btc=9_000)
    assert reb["acao"] == "VENDER"
    assert reb["ajuste"] < 0


def test_banda_de_tolerancia_evita_giro():
    """Dentro da banda o modelo manda não mexer — e o ajuste tem de ser zero."""
    alvo = cm.alvo_exposicao(50)
    patrimonio = 10_000.0
    quase_no_alvo = (alvo - 3) / 100 * patrimonio      # 3 p.p. abaixo do alvo
    reb = cm.plano_rebalanceamento(50, patrimonio, quase_no_alvo, banda=5.0)
    assert reb["acao"] == "MANTER"
    assert reb["ajuste"] == 0.0
    assert reb["dentro_da_banda"]

    # com banda menor que o desvio, volta a mandar ajustar
    reb2 = cm.plano_rebalanceamento(50, patrimonio, quase_no_alvo, banda=1.0)
    assert reb2["acao"] == "COMPRAR"


def test_rebalanceamento_converte_para_btc_quando_tem_preco():
    reb = cm.plano_rebalanceamento(20, 10_000, 2_000, preco_btc=50_000)
    assert reb["ajuste_btc"] == pytest.approx(reb["ajuste"] / 50_000)
    sem_preco = cm.plano_rebalanceamento(20, 10_000, 2_000)
    assert sem_preco["ajuste_btc"] is None


def test_aporte_segue_o_dca_da_fase():
    reb = cm.plano_rebalanceamento(10, 10_000, 5_000, aporte_base=1_000)
    assert reb["aporte_sugerido"] == pytest.approx(1_000 * cm.multiplicador_dca(10))


@pytest.mark.parametrize("patrimonio,em_btc", [(0, 100), (-5, 1), (1000, -1)])
def test_rebalanceamento_com_entrada_invalida(patrimonio, em_btc):
    assert cm.plano_rebalanceamento(50, patrimonio, em_btc)["acao"] == "—"


def test_rebalanceamento_com_score_invalido():
    assert cm.plano_rebalanceamento(float("nan"), 1000, 500)["acao"] == "—"


# ------------------------------------------------- histerese de fase

def test_fase_so_troca_com_margem():
    # 35 é a fronteira ACUMULAÇÃO -> EXPANSÃO; com margem 1.5 só vale a partir de 36.5
    assert cm.fase_confirmada(35.4, "ACUMULAÇÃO") == "ACUMULAÇÃO"
    assert cm.fase_confirmada(36.6, "ACUMULAÇÃO") == "EXPANSÃO"
    # descendo, idem: precisa entrar 1.5 abaixo de 35
    assert cm.fase_confirmada(34.0, "EXPANSÃO") == "EXPANSÃO"
    assert cm.fase_confirmada(33.0, "EXPANSÃO") == "ACUMULAÇÃO"


def test_fase_sem_estado_anterior():
    assert cm.fase_confirmada(50, None) == "EXPANSÃO"
    assert cm.fase_confirmada(50, "—") == "EXPANSÃO"


def test_fase_confirmada_com_score_invalido():
    assert cm.fase_confirmada(float("nan"), "EXPANSÃO") == "EXPANSÃO"


def test_histerese_nao_trava_mudanca_grande():
    """Pulo de duas fases não pode ficar preso pela margem."""
    assert cm.fase_confirmada(95, "EXPANSÃO") == "EUFORIA"
    assert cm.fase_confirmada(5, "DISTRIBUIÇÃO") == "FUNDO PROFUNDO"


def test_histerese_evita_alerta_diario():
    """
    Simula um score oscilando em cima do limiar: a fase só pode trocar uma
    vez, não a cada dia.
    """
    fase = "ACUMULAÇÃO"
    trocas = 0
    for score in [34.8, 35.2, 34.9, 35.5, 34.7, 35.1, 36.9, 35.2, 34.9]:
        nova = cm.fase_confirmada(score, fase)
        trocas += nova != fase
        fase = nova
    assert trocas == 1


# --------------------------------------------------- frescor dos dados

def _dados_cm_falsos(datas, mvrv_z=1.0):
    """Dataset mínimo no formato que `calcular` espera da Coin Metrics."""
    return pd.DataFrame({
        "date": datas,
        "price": np.linspace(10_000, 60_000, len(datas)),
        "cm_mvrv": np.full(len(datas), 2.0),
        "cm_mvrv_z": np.full(len(datas), float(mvrv_z)),
        "cm_nupl": np.full(len(datas), 0.4),
        "cm_puell": np.full(len(datas), 1.0),
    })


def test_idade_das_fontes(preco_sintetico):
    fim = preco_sintetico["date"].max()
    datas = pd.date_range(end=fim - pd.Timedelta(days=10), periods=400, freq="D")
    idades = cm.idade_das_fontes(
        {"sopr": pd.DataFrame({"date": datas, "valor": 1.0})},
        _dados_cm_falsos(datas), ate=fim)
    assert idades == {"cm": 10, "sopr": 10}


def test_onchain_recente_alimenta_o_pilar(preco_sintetico):
    """Atraso dentro do limite: o dado on-chain continua valendo."""
    fim = preco_sintetico["date"].max()
    datas = pd.date_range(end=fim - pd.Timedelta(days=2), periods=800, freq="D")
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      dados_cm=_dados_cm_falsos(datas))
    valuation = next(c for c in res["componentes"] if c["chave"] == "valuation")
    assert valuation["tipo"] == "on-chain"
    assert valuation["idade_dias"] == 2
    assert res["fontes_defasadas"] == []


def test_onchain_velho_cai_no_proxy_e_avisa(preco_sintetico):
    """
    O caso que motivou tudo isso: a fonte parou de publicar há meses. Casar
    esse MVRV com o preço de hoje seria pior do que não ter MVRV.
    """
    fim = preco_sintetico["date"].max()
    datas = pd.date_range(end=fim - pd.Timedelta(days=90), periods=800, freq="D")
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      dados_cm=_dados_cm_falsos(datas))

    valuation = next(c for c in res["componentes"] if c["chave"] == "valuation")
    assert valuation["tipo"] == "proxy"          # caiu no proxy de preço
    assert valuation["ok"]                       # e continua tendo leitura
    assert res["fonte"] == "proxy"
    assert res["atraso_max"] == 90
    assert res["fontes_defasadas"] == [{"fonte": "cm", "idade": 90}]
    assert 0 <= res["score"] <= 100


def test_card_avisa_o_atraso(preco_sintetico):
    fim = preco_sintetico["date"].max()
    datas = pd.date_range(end=fim - pd.Timedelta(days=45), periods=800, freq="D")
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      dados_cm=_dados_cm_falsos(datas))
    html = cm.card_html(res)
    assert "On-chain atrasado" in html
    assert "Coin Metrics (45d)" in html


def test_card_sem_aviso_quando_esta_em_dia(preco_sintetico):
    res = cm.calcular(preco_sintetico, fng_atual=50.0, series_onchain={},
                      usar_coinmetrics=False)
    assert "On-chain atrasado" not in cm.card_html(res)


def test_ffill_limitado_no_historico(preco_sintetico):
    """
    O corte também vale para o histórico: um buraco maior que o limite não
    pode ser preenchido com o último valor conhecido.
    """
    idx = pd.DatetimeIndex(preco_sintetico["date"])
    datas = idx[:-60]                                   # some nos últimos 60 dias
    series = cm.montar_series(preco_sintetico, None, None,
                              _dados_cm_falsos(datas))
    mvrv_z = series["cm_mvrv_z"]
    # carrega por MAX_DIAS_CARREGO dias e depois vira NaN
    assert np.isfinite(mvrv_z.iloc[-60 + cm.MAX_DIAS_CARREGO - 1])
    assert not np.isfinite(mvrv_z.iloc[-1])
