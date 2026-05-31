# -*- coding: utf-8 -*-
"""
03_duas_fontes.py
=================

Estende o script 02 acrescentando uma SEGUNDA linha de humor: a atenção
do público medida pelo Google Trends (termo "Bitcoin").

    Preço (Binance)  x  Fear & Greed  +  Google Trends

O Google Trends é OPCIONAL: se tomar rate limit, o script continua só com
o Fear & Greed (nunca quebra).

Saídas:
  - imprime a correlação preço x Fear&Greed e (se houver) preço x Trends
  - salva um PNG com 3 painéis: preço, Fear&Greed (desvio), Trends (desvio)

Rodar:
    python scripts/03_duas_fontes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import common  # noqa: E402


def plot_tres_paineis(df, tem_trends: bool, corr_fng: float,
                      corr_trends, png: str) -> None:
    """Gráfico com preço em cima e DUAS linhas de humor (desvio da média)."""
    plt.style.use("dark_background")

    n = 3 if tem_trends else 2
    alturas = [2, 1, 1] if tem_trends else [2, 1]
    fig, axes = plt.subplots(n, 1, figsize=(12, 8 if tem_trends else 7),
                             sharex=True, gridspec_kw={"height_ratios": alturas})

    ax_preco = axes[0]
    ax_fng = axes[1]

    # Painel 1: preço.
    ax_preco.plot(df["date"], df["price"], color="#f7931a", linewidth=1.6)
    titulo = f"BTC x Fear&Greed (corr={corr_fng:.3f})"
    if tem_trends and corr_trends == corr_trends:  # não-NaN
        titulo += f"  |  x Trends (corr={corr_trends:.3f})"
    ax_preco.set_title(titulo, color="white")
    ax_preco.set_ylabel("Preço BTC (USD)", color="#f7931a")
    ax_preco.grid(True, alpha=0.15)

    # Painel 2: Fear & Greed como desvio da média.
    dev_fng = common.desvio_da_media(df["fng"])
    ax_fng.bar(df["date"], dev_fng,
               color=["#26a69a" if v >= 0 else "#ef5350" for v in dev_fng], width=1.0)
    ax_fng.axhline(0, color="white", linewidth=0.6, alpha=0.5)
    ax_fng.set_ylabel("Fear&Greed\n(desvio)")
    ax_fng.grid(True, alpha=0.15)

    # Painel 3 (opcional): Google Trends como desvio da média.
    if tem_trends:
        ax_tr = axes[2]
        dev_tr = common.desvio_da_media(df["trends"])
        ax_tr.bar(df["date"], dev_tr,
                  color=["#42a5f5" if v >= 0 else "#7e57c2" for v in dev_tr], width=1.0)
        ax_tr.axhline(0, color="white", linewidth=0.6, alpha=0.5)
        ax_tr.set_ylabel("Google Trends\n(desvio)")
        ax_tr.grid(True, alpha=0.15)
        axes[2].set_xlabel("Data")
    else:
        ax_fng.set_xlabel("Data")

    fig.tight_layout()
    fig.savefig(png, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[Gráfico] PNG salvo em: {png}")


def main() -> None:
    print(">> [03] BTC x Fear&Greed + Google Trends\n")

    preco = common.fetch_btc_binance(dias=1500)
    fng = common.fetch_fear_greed(limit=0)
    if preco.empty or fng.empty:
        print("Preço ou Fear&Greed indisponível. Abortando.")
        return

    df = preco.merge(fng, on="date", how="inner").sort_values("date")
    corr_fng = common.correlacao(df["price"], df["fng"])
    print(f"   >>> Correlação preço x Fear&Greed: {corr_fng:.3f}")

    # --- Fonte OPCIONAL: Google Trends (pode falhar sem quebrar nada) ---
    trends = common.fetch_google_trends(termo="Bitcoin", timeframe="today 5-y")
    tem_trends = not trends.empty
    corr_trends = float("nan")

    if tem_trends:
        # Trends é semanal; reindexamos para diário e interpolamos.
        trends = trends.set_index("date").resample("D").interpolate().reset_index()
        df = df.merge(trends, on="date", how="left")
        df["trends"] = df["trends"].interpolate()
        corr_trends = common.correlacao(df["price"], df["trends"])
        print(f"   >>> Correlação preço x Google Trends: {corr_trends:.3f}")
    else:
        print("   (Google Trends indisponível — seguindo só com Fear&Greed.)")

    png = os.path.join(common.ROOT_DIR, "03_duas_fontes.png")
    plot_tres_paineis(df, tem_trends, corr_fng, corr_trends, png)
    print("\n>> Concluído.")


if __name__ == "__main__":
    main()
