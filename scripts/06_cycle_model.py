# -*- coding: utf-8 -*-
"""
06_cycle_model.py
=================

Roda o **BTC Cycle Model** (score de ciclo 0–100) no terminal e exporta o
card visual como um arquivo HTML que abre em qualquer navegador.

O que faz:
  1. Baixa o preço do BTC (histórico longo — a MA200W precisa de ~4 anos).
  2. Baixa o Fear & Greed Index.
  3. Busca as métricas on-chain SE houver `BGEO_API_KEY` (opcional; sem a
     chave o modelo usa os proxies grátis calculados do preço).
  4. Imprime o score, a fase do ciclo, a composição e o plano de posição.
  5. Salva `cycle_model.html` na raiz do projeto.

Rodar:
    python scripts/06_cycle_model.py
    python scripts/06_cycle_model.py --semanas 12   # mostra as N últimas semanas

Nada aqui é recomendação financeira.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common          # noqa: E402
import cycle_model as cm  # noqa: E402


def _barra(sub: float, largura: int = 24) -> str:
    """Barrinha de texto para o terminal (████░░░░)."""
    if sub != sub:  # NaN
        return "?" * largura
    cheio = int(round(max(0.0, min(100.0, sub)) / 100 * largura))
    return "█" * cheio + "░" * (largura - cheio)


def main() -> None:
    ap = argparse.ArgumentParser(description="BTC Cycle Model (score 0-100)")
    ap.add_argument("--semanas", type=int, default=8,
                    help="quantas semanas do histórico semanal imprimir")
    ap.add_argument("--dias", type=int, default=2200,
                    help="quantos dias de preço baixar (a MA200W pede ~1400)")
    args = ap.parse_args()

    print(">> [06] BTC Cycle Model — em que ponto do ciclo estamos?\n")

    preco = common.fetch_btc_price(dias=args.dias)
    if preco.empty:
        print("Não foi possível obter o preço do BTC. Abortando.")
        return
    print(f"   Preço: {len(preco)} dias ({preco['date'].min().date()} "
          f"-> {preco['date'].max().date()})")

    fng = common.fetch_fear_greed(limit=0)
    if not fng.empty:
        print(f"   Fear & Greed: {len(fng)} dias")

    if cm.tem_onchain():
        series_oc = cm.buscar_series_onchain()
        print(f"   On-chain (BGeometrics): {len(series_oc)} métricas "
              f"{sorted(series_oc) if series_oc else ''}")
    else:
        series_oc = {}
        print("   On-chain: sem chave (BGEO_API_KEY) — usando os proxies grátis")

    res = cm.calcular(preco, fng=fng if not fng.empty else None,
                      series_onchain=series_oc)

    # ---------------- saída no terminal ----------------
    print("\n" + "=" * 68)
    print(f"   SCORE DE CICLO: {res['score']:.1f}/100   ->   {res['fase']}")
    print(f"   {res['descricao']}")
    print("=" * 68 + "\n")

    print("   COMPOSIÇÃO DO SCORE")
    for c in res["componentes"]:
        sub = c["sub_score"]
        valor = "—" if c["valor"] != c["valor"] else f"{c['valor']:.3f}"
        print(f"   {c['nome']:<20} {_barra(sub)} "
              f"{'--' if sub != sub else f'{sub:5.1f}'}  "
              f"{c['rotulo']:<22} [{c['fonte']} · {c['tipo']} · valor {valor}]")

    plano = res["plano"]
    print(f"\n   PLANO: {plano['acao']}")
    print(f"   {plano['detalhe']}")
    print(f"   Exposição-alvo em BTC: {plano['exposicao_alvo']:.0f}%   |   "
          f"já realizado: {plano['realizado_alvo']:.0f}%   |   "
          f"DCA: {plano['dca']:.2f}× o aporte normal")
    if res["delta30"] == res["delta30"]:
        print(f"   Momentum do ciclo: {res['delta30']:+.1f} pontos em 30 dias")

    # ---------------- histórico semanal + backtest ----------------
    hist = cm.serie_score(preco, fng if not fng.empty else None, series_oc)
    sem = cm.serie_semanal(hist)
    if not sem.empty:
        print(f"\n   ÚLTIMAS {args.semanas} SEMANAS (o modelo é de ciclo: "
              "decida no fechamento semanal)")
        for r in sem.tail(args.semanas).itertuples():
            print(f"   {r.date.date()}  ${r.price:>10,.0f}  "
                  f"score {r.score:5.1f}  {r.fase}")

    bt = cm.backtest_exposicao(hist)
    if bt:
        m, h = bt["modelo"], bt["hold"]
        print("\n   BACKTEST (exposição pela curva do score vs. comprar e segurar)")
        print(f"   {'':<12}{'retorno':>12}{'CAGR':>10}{'drawdown':>12}{'Sharpe':>9}")
        print(f"   {'modelo':<12}{m['ret_total']:>11.1%}{m['cagr']:>10.1%}"
              f"{m['dd_max']:>12.1%}{m['sharpe']:>9.2f}")
        print(f"   {'buy & hold':<12}{h['ret_total']:>11.1%}{h['cagr']:>10.1%}"
              f"{h['dd_max']:>12.1%}{h['sharpe']:>9.2f}")
        print(f"   Exposição média do modelo: {m['exposicao_media']:.0f}%")

    # ---------------- card HTML ----------------
    destino = os.path.join(common.ROOT_DIR, "cycle_model.html")
    try:
        with open(destino, "w", encoding="utf-8") as f:
            f.write(cm.pagina_html(res))
        print(f"\n   Card salvo em: {destino}")
    except Exception as e:
        print(f"\n   Não foi possível salvar o HTML: {e}")

    print("\n   ⚠️  Conteúdo educativo — não é recomendação financeira.")
    print(">> Concluído.")


if __name__ == "__main__":
    main()
