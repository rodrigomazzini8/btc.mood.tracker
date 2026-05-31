# -*- coding: utf-8 -*-
"""
02_indices_gratis.py
====================

Versão MAIS SIMPLES e robusta do projeto (não depende de IA pesada nem
de chave de API):

    Preço do BTC (Binance)  x  Fear & Greed Index (alternative.me)

O que faz:
  1. Baixa o preço diário do BTC.
  2. Baixa o índice de Medo & Ganância (0–100).
  3. Junta as duas séries por data.
  4. Calcula a correlação de Pearson e imprime no terminal.
  5. Salva um PNG (preço em cima, humor embaixo como desvio da média).

Rodar:
    python scripts/02_indices_gratis.py
"""

import os
import sys

# Garante que conseguimos importar common.py mesmo rodando de qualquer lugar.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402


def main() -> None:
    print(">> [02] BTC x Fear & Greed (fontes grátis, sem chave)\n")

    # 1) Preço do BTC na Binance (paginado).
    preco = common.fetch_btc_binance(dias=1500)
    if preco.empty:
        print("Não foi possível obter o preço do BTC. Abortando.")
        return
    print(f"   Preço: {len(preco)} dias ({preco['date'].min().date()} "
          f"-> {preco['date'].max().date()})")

    # 2) Humor histórico: Fear & Greed Index.
    fng = common.fetch_fear_greed(limit=0)
    if fng.empty:
        print("Não foi possível obter o Fear & Greed. Abortando.")
        return
    print(f"   Fear&Greed: {len(fng)} dias")

    # 3) Junta as duas séries por data (interseção das datas disponíveis).
    df = preco.merge(fng, on="date", how="inner").sort_values("date")
    if df.empty:
        print("Sem datas em comum entre preço e humor. Abortando.")
        return

    # 4) Correlação preço x humor.
    corr = common.correlacao(df["price"], df["fng"])
    print(f"\n   >>> Correlação (preço x Fear&Greed): {corr:.3f}")
    if corr > 0:
        print("   Interpretação: humor mais 'ganancioso' anda junto com preço maior.")
    else:
        print("   Interpretação: relação fraca/invertida no período.")

    # 5) Gráfico PNG.
    png = os.path.join(common.ROOT_DIR, "02_indices_gratis.png")
    common.plot_preco_e_humor(
        df, col_humor="fng",
        titulo="BTC x Fear & Greed Index",
        arquivo_png=png, label_humor="Fear & Greed", corr=corr,
    )
    print("\n>> Concluído.")


if __name__ == "__main__":
    main()
