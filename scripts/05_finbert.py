# -*- coding: utf-8 -*-
"""
05_finbert.py
=============

Versão AVANÇADA da "IA": usa o FinBERT (ProsusAI/finbert), um modelo
Transformer ajustado para textos financeiros, para classificar posts reais
do Reddit em positivo / neutro / negativo.

Conversão para nota:
    nota_do_post = P(positivo)*(+1) + P(neutro)*(0) + P(negativo)*(-1)
ou seja, é a probabilidade ponderada pela confiança do modelo, entre -1 e +1.

Depois cruzamos a nota média diária com o preço do BTC.

ATENÇÃO: na primeira execução o modelo (~440 MB) é baixado pelo
huggingface/transformers. Não precisa de chave. Se transformers/torch não
estiverem instalados, o script avisa e sai com elegância.

Rodar:
    python scripts/05_finbert.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import common  # noqa: E402


def carregar_finbert():
    """
    Carrega o pipeline do FinBERT. Devolve (pipeline, None) em sucesso ou
    (None, mensagem_de_erro) se as dependências/modelo não estiverem ok.
    """
    try:
        from transformers import (AutoTokenizer,
                                  AutoModelForSequenceClassification,
                                  TextClassificationPipeline)
    except Exception as e:
        return None, f"transformers/torch não instalados: {e}"

    try:
        nome = "ProsusAI/finbert"
        tok = AutoTokenizer.from_pretrained(nome)
        modelo = AutoModelForSequenceClassification.from_pretrained(nome)
        # return_all_scores=True -> probabilidade de cada classe.
        pipe = TextClassificationPipeline(
            model=modelo, tokenizer=tok, top_k=None, truncation=True)
        return pipe, None
    except Exception as e:
        return None, f"falha ao baixar/instanciar o FinBERT: {e}"


def nota_finbert(pipe, texto: str) -> float:
    """
    Devolve a nota ponderada (-1..+1) de um texto usando o FinBERT.

    O pipeline devolve algo como:
        [{'label': 'positive', 'score': 0.9}, {'label': 'neutral', ...}, ...]
    """
    try:
        resultado = pipe(texto[:512])  # FinBERT aceita ~512 tokens
    except Exception:
        return 0.0

    # Dependendo da versão, vem aninhado em lista.
    scores = resultado[0] if isinstance(resultado[0], list) else resultado

    pesos = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    nota = 0.0
    for item in scores:
        nota += pesos.get(item["label"].lower(), 0.0) * item["score"]
    return nota


def main() -> None:
    print(">> [05] BTC x Sentimento Reddit (FinBERT)\n")

    # 1) Carrega o modelo (pode demorar / baixar na 1ª vez).
    print("   Carregando FinBERT (pode baixar o modelo na 1ª vez)...")
    pipe, erro = carregar_finbert()
    if pipe is None:
        print(f"   FinBERT indisponível: {erro}")
        print("   Dica: rode os scripts 02/03/04, que não precisam de IA pesada.")
        return

    # 2) Texto real do Reddit.
    posts = common.fetch_reddit_posts(subreddit="Bitcoin", listing="new", limit=100)
    if posts.empty:
        print("   Sem posts do Reddit (rate limit?). Abortando.")
        return
    print(f"   Reddit: {len(posts)} posts. Classificando com FinBERT...")

    # 3) Classifica cada post e tira a média diária.
    notas = []
    for _, row in posts.iterrows():
        texto = f"{row['title']}. {row['text']}".strip()
        if not texto:
            continue
        notas.append({"date": row["date"].normalize(),
                      "finbert": nota_finbert(pipe, texto)})

    if not notas:
        print("   Nenhum texto classificável. Abortando.")
        return

    sent = (pd.DataFrame(notas)
            .groupby("date", as_index=False)["finbert"].mean())
    print(f"   Nota média FinBERT (período): {sent['finbert'].mean():.3f}")

    # 4) Cruza com o preço.
    preco = common.fetch_btc_binance(dias=60)
    if preco.empty:
        print("   Preço indisponível. Abortando.")
        return

    df = preco.merge(sent, on="date", how="inner").sort_values("date")
    corr = common.correlacao(df["price"], df["finbert"]) if len(df) >= 3 else float("nan")
    print(f"\n   >>> Correlação (preço x FinBERT): {corr:.3f}")
    if len(df) < 3:
        print("   (Reddit traz só posts recentes; poucos dias para correlação.)")

    # 5) Gráfico.
    if df.empty:
        print("   Sem datas em comum para plotar.")
        return
    png = os.path.join(common.ROOT_DIR, "05_finbert.png")
    common.plot_preco_e_humor(
        df, col_humor="finbert",
        titulo="BTC x Sentimento Reddit (FinBERT)",
        arquivo_png=png, label_humor="FinBERT", corr=corr,
    )
    print("\n>> Concluído. (Não é recomendação financeira.)")


if __name__ == "__main__":
    main()
