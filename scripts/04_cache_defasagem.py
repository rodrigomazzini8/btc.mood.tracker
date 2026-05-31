# -*- coding: utf-8 -*-
"""
04_cache_defasagem.py
=====================

Foco deste script: CACHE EM CSV + MÉDIA MÓVEL + CORRELAÇÃO DEFASADA.

Pergunta de pesquisa (didática): "o humor de hoje ANTECIPA o preço de
amanhã?" Para investigar, testamos a correlação entre preço e o humor
deslocado por vários lags (0 a 14 dias).

Recursos:
  - Salva preço e Fear&Greed em cache/ (CSV) para não rebaixar tudo a cada
    execução. Se o cache for recente (< 12h), reutiliza.
  - Suaviza o humor com média móvel de 7 dias.
  - Calcula a correlação defasada e mostra qual lag dá o pico.

Rodar:
    python scripts/04_cache_defasagem.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import common  # noqa: E402

# Quanto tempo o cache é considerado "fresco" (segundos). 12 horas.
CACHE_TTL = 12 * 60 * 60


def carregar_com_cache(nome: str, baixar_fn) -> pd.DataFrame:
    """
    Carrega um DataFrame do cache CSV se ele existir e for recente;
    caso contrário baixa com `baixar_fn`, salva no cache e devolve.

    Se o download falhar mas houver cache antigo, usa o cache antigo
    (melhor dado velho do que programa quebrado).
    """
    common.garantir_cache_dir()
    caminho = os.path.join(common.CACHE_DIR, nome)

    fresco = (os.path.exists(caminho)
              and (time.time() - os.path.getmtime(caminho)) < CACHE_TTL)
    if fresco:
        print(f"   [cache] usando {nome} (recente)")
        return pd.read_csv(caminho, parse_dates=["date"])

    print(f"   [cache] baixando {nome} ...")
    df = baixar_fn()
    if not df.empty:
        df.to_csv(caminho, index=False)
        return df

    # Download falhou: tenta cache antigo como fallback.
    if os.path.exists(caminho):
        print(f"   [cache] download falhou; usando {nome} antigo")
        return pd.read_csv(caminho, parse_dates=["date"])

    return df  # vazio


def main() -> None:
    print(">> [04] Cache CSV + média móvel + correlação defasada\n")

    preco = carregar_com_cache("preco_btc.csv",
                               lambda: common.fetch_btc_price(dias=1500))
    fng = carregar_com_cache("fear_greed.csv",
                             lambda: common.fetch_fear_greed(limit=0))
    if preco.empty or fng.empty:
        print("Dados insuficientes. Abortando.")
        return

    df = preco.merge(fng, on="date", how="inner").sort_values("date").reset_index(drop=True)

    # Média móvel de 7 dias suaviza o ruído do humor diário.
    df["fng_mm7"] = df["fng"].rolling(7, min_periods=1).mean()

    # Correlação simples (lag 0).
    corr0 = common.correlacao(df["price"], df["fng_mm7"])
    print(f"   Correlação simples (lag 0): {corr0:.3f}")

    # Correlação defasada: humor de N dias atrás vs preço de hoje.
    tab = common.correlacao_defasada(df["price"], df["fng_mm7"], max_lag=14)
    # idxmax pela magnitude (pode ser correlação negativa forte).
    melhor = tab.iloc[tab["corr"].abs().idxmax()]
    print(f"   Melhor lag: {int(melhor['lag'])} dia(s) -> corr = {melhor['corr']:.3f}")
    if melhor["lag"] > 0:
        print("   (Leitura otimista: humor passado se relaciona com preço atual.)")
    print("   AVISO: correlação NÃO é causalidade. Não é recomendação financeira.")

    # --- Gráfico: preço + humor(desvio) em cima/baixo, e curva de lags ---
    plt.style.use("dark_background")
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(12, 9),
        gridspec_kw={"height_ratios": [2, 1, 1]})

    ax1.plot(df["date"], df["price"], color="#f7931a", linewidth=1.6)
    ax1.set_title(f"BTC x Fear&Greed (MM7)  —  corr lag0={corr0:.3f}", color="white")
    ax1.set_ylabel("Preço BTC (USD)", color="#f7931a")
    ax1.grid(True, alpha=0.15)

    dev = common.desvio_da_media(df["fng_mm7"])
    ax2.bar(df["date"], dev,
            color=["#26a69a" if v >= 0 else "#ef5350" for v in dev], width=1.0)
    ax2.axhline(0, color="white", linewidth=0.6, alpha=0.5)
    ax2.set_ylabel("Fear&Greed MM7\n(desvio)")
    ax2.grid(True, alpha=0.15)

    # Curva de correlação por lag (quanto o humor antecipa o preço).
    ax3.plot(tab["lag"], tab["corr"], marker="o", color="#42a5f5")
    ax3.axhline(0, color="white", linewidth=0.6, alpha=0.5)
    ax3.set_ylabel("Correlação")
    ax3.set_xlabel("Lag (dias que o humor antecede o preço)")
    ax3.grid(True, alpha=0.15)

    fig.tight_layout()
    png = os.path.join(common.ROOT_DIR, "04_cache_defasagem.png")
    fig.savefig(png, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\n[Gráfico] PNG salvo em: {png}")
    print(">> Concluído.")


if __name__ == "__main__":
    main()
