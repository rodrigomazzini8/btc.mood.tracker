# -*- coding: utf-8 -*-
"""
07_calibracao.py
================

Confere o **BTC Cycle Model** contra a história real do Bitcoin: imprime o
score que o modelo teria dado em cada topo e fundo de ciclo, com e sem os
dados on-chain, e resume a distribuição do score.

É o script que sustenta os limiares das escalas em `cycle_model.py` — se
você mudar uma escala, rode este arquivo para ver o efeito nas viradas
históricas antes de confiar no número de hoje.

Fonte dos dados: **Coin Metrics Community** (CSV público no GitHub, grátis e
sem chave) — traz preço, market cap, realized cap (via MVRV) e emissão desde
2010, o que permite reconstruir MVRV, MVRV Z-Score, NUPL e Puell reais.

Rodar:
    python scripts/07_calibracao.py
    python scripts/07_calibracao.py --so-proxy   # ignora on-chain, testa os proxies

O que esperar de um modelo bem calibrado:
  - topos de ciclo em DISTRIBUIÇÃO ou EUFORIA (score alto);
  - fundos de ciclo em FUNDO PROFUNDO ou ACUMULAÇÃO (score baixo);
  - o score andando por toda a faixa 0-100 ao longo da história, sem
    saturar num extremo.

Nada aqui é recomendação financeira.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import cycle_model as cm  # noqa: E402


# Viradas de ciclo (datas do fechamento diário, conferidas na série real).
MARCOS = [
    ("TOPO  nov/2013", "2013-11-30", "topo"),
    ("FUNDO jan/2015", "2015-01-14", "fundo"),
    ("TOPO  dez/2017", "2017-12-17", "topo"),
    ("FUNDO dez/2018", "2018-12-15", "fundo"),
    ("CRASH covid/20", "2020-03-13", "fundo"),
    ("TOPO  abr/2021", "2021-04-14", "topo"),
    ("TOPO  nov/2021", "2021-11-09", "topo"),
    ("FUNDO nov/2022", "2022-11-21", "fundo"),
    ("TOPO  mar/2024", "2024-03-14", "topo"),
    ("TOPO  out/2025", "2025-10-06", "topo"),
    ("FUNDO fev/2026", "2026-02-05", "fundo"),
]

FASES_OK = {
    "topo": {"DISTRIBUIÇÃO", "EUFORIA"},
    "fundo": {"FUNDO PROFUNDO", "ACUMULAÇÃO"},
}


def _linha_hist(hist: pd.DataFrame, data: str):
    """Score do modelo na data (ou a última anterior a ela)."""
    if hist.empty:
        return None
    d = pd.Timestamp(data)
    antes = hist[hist["date"] <= d]
    return antes.iloc[-1] if not antes.empty else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibração do BTC Cycle Model")
    ap.add_argument("--so-proxy", action="store_true",
                    help="ignora o on-chain e testa só os proxies de preço")
    args = ap.parse_args()

    print(">> [07] Calibração do BTC Cycle Model contra a história real\n")

    dados = cm.fetch_coinmetrics()
    if dados.empty:
        print("Não foi possível obter os dados da Coin Metrics (sem rede?).")
        return
    preco = dados[["date", "price"]].copy()
    print(f"   Série: {len(preco)} dias "
          f"({preco['date'].min().date()} -> {preco['date'].max().date()})")

    modos = [("proxies (só preço)", None)]
    if not args.so_proxy:
        modos.append(("on-chain (Coin Metrics)", dados))

    historicos = {}
    for nome, cm_dados in modos:
        historicos[nome] = cm.serie_score(preco, dados_cm=cm_dados)

    # ---------------- score em cada virada de ciclo ----------------
    print("\n   SCORE DO MODELO NAS VIRADAS DE CICLO")
    cab = f"   {'marco':<16}{'preço':>10}"
    for nome in historicos:
        cab += f"{nome[:22]:>26}"
    print(cab)

    acertos = {nome: 0 for nome in historicos}
    total = 0
    for rotulo, data, tipo in MARCOS:
        linhas = {nome: _linha_hist(h, data) for nome, h in historicos.items()}
        if all(v is None for v in linhas.values()):
            continue
        total += 1
        alguma = next(v for v in linhas.values() if v is not None)
        txt = f"   {rotulo:<16}{alguma['price']:>10,.0f}"
        for nome, linha in linhas.items():
            if linha is None:
                txt += f"{'—':>26}"
                continue
            fase = cm.fase_do_score(linha["score"])
            ok = "ok " if fase in FASES_OK[tipo] else "XX "
            acertos[nome] += 1 if fase in FASES_OK[tipo] else 0
            txt += f"{ok + f'{linha.score:5.1f} ' + fase:>26}"
        print(txt)

    print(f"\n   Acertos de fase (topo em distribuição/euforia, "
          f"fundo em fundo/acumulação), de {total} marcos:")
    for nome, n in acertos.items():
        print(f"     {nome:<26} {n}/{total}")

    # ---------------- distribuição do score ----------------
    print("\n   DISTRIBUIÇÃO DO SCORE (o modelo usa toda a faixa 0-100?)")
    for nome, h in historicos.items():
        s = h["score"]
        print(f"     {nome:<26} min {s.min():5.1f} | p10 {s.quantile(.1):5.1f} | "
              f"mediana {s.median():5.1f} | p90 {s.quantile(.9):5.1f} | "
              f"max {s.max():5.1f}")

    print("\n   TEMPO EM CADA FASE")
    for nome, h in historicos.items():
        fases = h["score"].map(cm.fase_do_score).value_counts(normalize=True) * 100
        partes = " · ".join(f"{f}: {p:.0f}%" for f, p in fases.items())
        print(f"     {nome:<26} {partes}")

    # ---------------- backtest ----------------
    print("\n   BACKTEST (exposição pela curva do score vs. comprar e segurar)")
    print("   O recorte importa: nos primeiros anos QUALQUER redução de "
          "exposição perde feio\n   para o buy & hold, porque a tendência de "
          "15 anos domina o ciclo.")
    for nome, h in historicos.items():
        print(f"\n     {nome}")
        print(f"     {'período':<12}{'CAGR modelo':>13}{'CAGR hold':>11}"
              f"{'DD modelo':>11}{'DD hold':>9}{'Sharpe mod':>12}{'Sharpe hold':>13}")
        for ini in ("2011", "2015", "2018", "2021"):
            sub = h[h["date"] >= f"{ini}-01-01"].reset_index(drop=True)
            bt = cm.backtest_exposicao(sub)
            if not bt:
                continue
            m, hold = bt["modelo"], bt["hold"]
            print(f"     desde {ini:<7}{m['cagr']*100:>12.1f}%{hold['cagr']*100:>10.1f}%"
                  f"{m['dd_max']*100:>10.1f}%{hold['dd_max']*100:>8.1f}%"
                  f"{m['sharpe']:>12.2f}{hold['sharpe']:>13.2f}")

    # ---------------- termômetro (-2..+2) ----------------
    calibrar_termometro(preco)

    print("\n   ⚠️  Calibrado com o passado. Os limiares de fundo e topo mudam "
          "a cada ciclo.\n   Não é recomendação financeira.")


# Faixas ANTES da recalibração — ficam aqui só para mostrar o problema que a
# conferência com dados reais revelou (nos topos de 2024 e 2025 elas diziam
# "NEUTRO"). O código de produção usa `termometro.FAIXAS`.
FAIXAS_ANTIGAS = {
    "mayer":      [(0.8, 2), (1.0, 1), (1.5, 0), (2.4, -1), (float("inf"), -2)],
    "ma200w":     [(1.0, 2), (1.5, 1), (3.0, 0), (5.0, -1), (float("inf"), -2)],
    "rsi_mensal": [(30, 2), (45, 1), (60, 0), (70, -1), (float("inf"), -2)],
    "mvrv":       [(1.0, 2), (1.5, 1), (2.5, 0), (3.5, -1), (float("inf"), -2)],
    "mvrv_z":     [(0.0, 2), (2.0, 1), (4.0, 0), (6.0, -1), (float("inf"), -2)],
    "nupl":       [(0.0, 2), (0.25, 1), (0.5, 0), (0.75, -1), (float("inf"), -2)],
    "puell":      [(0.5, 2), (1.0, 1), (2.0, 0), (4.0, -1), (float("inf"), -2)],
}

SINAL_OK = {
    "topo": {"VENDA", "VENDA FORTE"},
    "fundo": {"COMPRA", "COMPRA FORTE"},
}


def calibrar_termometro(preco: pd.DataFrame) -> None:
    """
    Mesma conferência, agora para o Termômetro (-2..+2): qual sinal ele teria
    dado em cada virada, com as faixas novas e com as antigas.
    """
    import termometro as term

    print("\n   TERMÔMETRO (-2..+2) NAS MESMAS VIRADAS")
    hist = term.serie_score_historico(preco, fng=None, incluir_onchain=True)
    if hist.empty:
        print("     (sem histórico suficiente)")
        return

    faixas_novas = dict(term.FAIXAS)
    acertos = {"faixas novas": 0, "faixas antigas": 0}
    total = 0
    print(f"   {'marco':<16}{'preço':>10}{'faixas novas':>24}{'faixas antigas':>24}")
    for rotulo, data, tipo in MARCOS:
        linha = _linha_hist(hist, data)
        if linha is None:
            continue
        total += 1
        txt = f"   {rotulo:<16}{linha['price']:>10,.0f}"
        for nome, faixas in (("faixas novas", faixas_novas),
                             ("faixas antigas", FAIXAS_ANTIGAS)):
            term.FAIXAS = {**faixas_novas, **faixas}  # antigas só onde existem
            h = term.serie_score_historico(preco, fng=None, incluir_onchain=True)
            l2 = _linha_hist(h, data)
            sinal = term.score_para_sinal(l2["score"])
            ok = "ok " if sinal in SINAL_OK[tipo] else "XX "
            acertos[nome] += 1 if sinal in SINAL_OK[tipo] else 0
            txt += f"{ok + f'{l2.score:5.2f} ' + sinal:>24}"
        print(txt)
    term.FAIXAS = faixas_novas  # devolve as faixas de produção

    print(f"\n   Acertos de sinal (topo em venda, fundo em compra), "
          f"de {total} marcos:")
    for nome, n in acertos.items():
        print(f"     {nome:<16} {n}/{total}")


if __name__ == "__main__":
    main()
