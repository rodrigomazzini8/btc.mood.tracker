# -*- coding: utf-8 -*-
"""
01_simples_vader.py
===================

Versão DIDÁTICA com a "IA mais simples": análise de sentimento por regras
usando o VADER, cruzada com o preço do BTC.

    Preço do BTC (Binance)  x  Sentimento dos posts do Reddit (VADER)

Importante: o Reddit .json só devolve posts RECENTES, então o sentimento
aqui cobre poucos dias. O objetivo é demonstrar o método, não fazer
ciência com histórico longo (para histórico, use o script 02).

Rodar:
    python scripts/01_simples_vader.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import common  # noqa: E402


def analisar_sentimento_vader(posts: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica o VADER em cada post (título + texto) e devolve uma nota diária.

    O VADER devolve um score 'compound' entre -1 (muito negativo) e
    +1 (muito positivo). Tiramos a média por dia.
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except Exception as e:
        print(f"[VADER] Biblioteca indisponível: {e}")
        return pd.DataFrame(columns=["date", "sentimento"])

    analyzer = SentimentIntensityAnalyzer()

    notas = []
    for _, row in posts.iterrows():
        texto = f"{row['title']} {row['text']}".strip()
        if not texto:
            continue
        score = analyzer.polarity_scores(texto)["compound"]
        notas.append({"date": row["date"].normalize(), "sentimento": score})

    if not notas:
        return pd.DataFrame(columns=["date", "sentimento"])

    df = pd.DataFrame(notas)
    # Média diária do sentimento.
    return df.groupby("date", as_index=False)["sentimento"].mean()


def main() -> None:
    print(">> [01] BTC x Sentimento do Reddit (VADER)\n")

    # 1) Preço do BTC (só precisamos dos últimos dias para casar com o Reddit).
    preco = common.fetch_btc_price(dias=60)
    if preco.empty:
        print("Não foi possível obter o preço do BTC. Abortando.")
        return

    # 2) Posts recentes do Reddit (fonte de texto p/ a IA).
    posts = common.fetch_reddit_posts(subreddit="Bitcoin", listing="new", limit=100)
    if posts.empty:
        print("Não foi possível obter posts do Reddit (rate limit?). Abortando.")
        return
    print(f"   Reddit: {len(posts)} posts recentes")

    # 3) Sentimento diário via VADER.
    sent = analisar_sentimento_vader(posts)
    if sent.empty:
        print("Sem sentimento calculável. Abortando.")
        return
    print(f"   Sentimento médio (período): {sent['sentimento'].mean():.3f}")

    # 4) Junta preço x sentimento pelas datas em comum.
    df = preco.merge(sent, on="date", how="inner").sort_values("date")
    if len(df) < 3:
        # Reddit cobre poucos dias: a correlação pode não ser calculável.
        print("\n   Poucos dias em comum (Reddit só traz posts recentes).")
        print("   Mostrando o gráfico mesmo assim; correlação pode ser NaN.")

    corr = common.correlacao(df["price"], df["sentimento"]) if len(df) >= 3 else float("nan")
    print(f"\n   >>> Correlação (preço x sentimento VADER): {corr:.3f}")

    # 5) Gráfico.
    if df.empty:
        print("Sem datas em comum para plotar.")
        return
    png = os.path.join(common.ROOT_DIR, "01_simples_vader.png")
    common.plot_preco_e_humor(
        df, col_humor="sentimento",
        titulo="BTC x Sentimento Reddit (VADER)",
        arquivo_png=png, label_humor="Sentimento", corr=corr,
    )
    print("\n>> Concluído.")


if __name__ == "__main__":
    main()
