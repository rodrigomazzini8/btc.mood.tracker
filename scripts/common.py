# -*- coding: utf-8 -*-
"""
common.py — Funções compartilhadas pelos scripts do btc-mood-tracker.

Tudo aqui usa SOMENTE fontes gratuitas e sem chave de API:
  - Preço do BTC: Binance (klines diários, paginado por endTime)
  - Humor histórico: Fear & Greed Index da alternative.me
  - Atenção: Google Trends via pytrends (termo "Bitcoin")
  - Texto p/ IA: posts do Reddit via endpoints .json públicos

Filosofia: nenhuma fonte OPCIONAL pode derrubar o programa. Tudo que
pode falhar (rede, rate limit) é protegido com try/except e mensagens
claras. As funções devolvem DataFrames vazios em caso de falha.

Os comentários são didáticos e em português de propósito. :)
"""

from __future__ import annotations

import os
import time
import datetime as dt

import requests
import pandas as pd

# --------------------------------------------------------------------------
# Caminhos / constantes
# --------------------------------------------------------------------------

# Pasta de cache (CSV). Fica na raiz do projeto, irmã de scripts/.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT_DIR, "cache")

# User-Agent é OBRIGATÓRIO para o Reddit não bloquear a requisição.
USER_AGENT = "btc-mood-tracker/1.0 (educational; +https://github.com)"

# Timeout padrão de rede (segundos). Curto para não travar o programa.
HTTP_TIMEOUT = 20


def garantir_cache_dir() -> str:
    """Cria a pasta cache/ se não existir e devolve seu caminho."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


# --------------------------------------------------------------------------
# 1) PREÇO DO BTC — Binance (grátis, sem chave)
# --------------------------------------------------------------------------

def fetch_btc_binance(dias: int = 1500, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Baixa o preço diário do BTC na Binance.

    A Binance limita cada chamada de /klines a 1000 candles. Para pegar
    mais de 1000 dias, paginamos: começamos no passado e avançamos usando
    o parâmetro `endTime`/`startTime` até cobrir o período pedido.

    Retorna um DataFrame com colunas: ['date', 'price'] (preço de fechamento).
    Em caso de erro de rede devolve DataFrame vazio (programa não quebra).
    """
    url = "https://api.binance.com/api/v3/klines"

    # Quanto tempo no passado queremos começar (com folga).
    fim_ms = int(time.time() * 1000)
    inicio_ms = fim_ms - dias * 24 * 60 * 60 * 1000

    todas = []
    cursor = inicio_ms

    # Cada página traz até 1000 candles diários. Avançamos o cursor.
    while cursor < fim_ms:
        params = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": cursor,
            "limit": 1000,
        }
        try:
            r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            lote = r.json()
        except Exception as e:  # rede, JSON, HTTP... nada pode quebrar tudo
            print(f"[Binance] Falha ao baixar preço: {e}")
            break

        if not lote:
            break

        todas.extend(lote)

        # O campo [6] é o "close time" do último candle; avançamos +1ms.
        ultimo_close = lote[-1][6]
        novo_cursor = ultimo_close + 1
        if novo_cursor <= cursor:  # proteção contra loop infinito
            break
        cursor = novo_cursor

        # Educação com a API pública: pequena pausa entre páginas.
        time.sleep(0.2)

    if not todas:
        return pd.DataFrame(columns=["date", "price"])

    # Estrutura de cada kline da Binance:
    # [openTime, open, high, low, close, volume, closeTime, ...]
    df = pd.DataFrame(todas, columns=[
        "openTime", "open", "high", "low", "close", "volume",
        "closeTime", "qav", "trades", "tbbav", "tbqav", "ignore",
    ])
    df["date"] = pd.to_datetime(df["openTime"], unit="ms").dt.normalize()
    df["price"] = df["close"].astype(float)
    df = df[["date", "price"]].drop_duplicates("date").sort_values("date")
    return df.reset_index(drop=True)


def fetch_btc_coingecko(dias: int = 365) -> pd.DataFrame:
    """
    Baixa o preço diário do BTC na CoinGecko (grátis, sem chave).

    Serve de FALLBACK: a Binance bloqueia requisições de servidores nos EUA
    (HTTP 451), o que quebra o app quando hospedado em nuvem (ex.: Streamlit
    Community Cloud, que roda nos EUA). A CoinGecko funciona desses servidores.

    Observação: o plano público/grátis costuma limitar o histórico a ~365
    dias, então capamos `dias` em 365. Para >90 dias a granularidade já vem
    diária automaticamente.

    Retorna DataFrame ['date', 'price'] ou vazio em caso de erro.
    """
    dias = min(int(dias), 365)  # limite prático do tier grátis
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    params = {"vs_currency": "usd", "days": dias}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        precos = r.json().get("prices", [])
    except Exception as e:
        print(f"[CoinGecko] Falha ao baixar preço: {e}")
        return pd.DataFrame(columns=["date", "price"])

    if not precos:
        return pd.DataFrame(columns=["date", "price"])

    # Cada item é [timestamp_ms, preco]. Normalizamos para 1 ponto por dia
    # (pegando o último preço de cada dia).
    df = pd.DataFrame(precos, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.normalize()
    df = (df.sort_values("ts")
            .groupby("date", as_index=False)["price"].last())
    return df[["date", "price"]].reset_index(drop=True)


def fetch_btc_price(dias: int = 1500, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Preço do BTC com FALLBACK robusto: tenta a Binance (histórico longo,
    ótima localmente) e, se falhar/vier vazia (ex.: bloqueio 451 na nuvem),
    cai para a CoinGecko. Use ESTA função no lugar de chamar a Binance direto.
    """
    df = fetch_btc_binance(dias=dias, symbol=symbol)
    if not df.empty:
        return df
    print("[Preço] Binance indisponível — tentando CoinGecko...")
    return fetch_btc_coingecko(dias=dias)


# --------------------------------------------------------------------------
# 2) HUMOR HISTÓRICO — Fear & Greed Index (alternative.me)
# --------------------------------------------------------------------------

def fetch_fear_greed(limit: int = 0) -> pd.DataFrame:
    """
    Baixa o Fear & Greed Index (0–100) da alternative.me.

    limit=0 traz TODO o histórico disponível (desde 2018).
    0   = medo extremo  (vermelho)
    100 = ganância extrema (verde)

    Retorna DataFrame com colunas ['date', 'fng'] ou vazio em caso de erro.
    """
    url = "https://api.alternative.me/fng/"
    try:
        r = requests.get(url, params={"limit": limit, "format": "json"},
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        dados = r.json().get("data", [])
    except Exception as e:
        print(f"[Fear&Greed] Falha ao baixar índice: {e}")
        return pd.DataFrame(columns=["date", "fng"])

    if not dados:
        return pd.DataFrame(columns=["date", "fng"])

    df = pd.DataFrame(dados)
    # 'timestamp' vem como segundos epoch em string.
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.normalize()
    df["fng"] = df["value"].astype(float)
    df = df[["date", "fng"]].sort_values("date")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# 3) ATENÇÃO — Google Trends via pytrends (OPCIONAL: pode falhar)
# --------------------------------------------------------------------------

def fetch_google_trends(termo: str = "Bitcoin", timeframe: str = "today 5-y") -> pd.DataFrame:
    """
    Baixa o interesse de busca no Google Trends para um termo.

    O Google Trends NÃO é uma API oficial; o pytrends faz scraping e
    frequentemente toma rate limit (HTTP 429). Por isso essa fonte é
    OPCIONAL: se falhar, devolvemos DataFrame vazio e o programa segue.

    Retorna DataFrame ['date', 'trends'] (0–100) ou vazio.
    """
    try:
        from pytrends.request import TrendReq
    except Exception as e:
        print(f"[Trends] pytrends não instalado: {e}")
        return pd.DataFrame(columns=["date", "trends"])

    try:
        py = TrendReq(hl="en-US", tz=0)
        py.build_payload([termo], timeframe=timeframe)
        df = py.interest_over_time()
    except Exception as e:
        # Rate limit (429) cai aqui. Não é fatal.
        print(f"[Trends] Indisponível (provável rate limit): {e}")
        return pd.DataFrame(columns=["date", "trends"])

    if df is None or df.empty or termo not in df.columns:
        return pd.DataFrame(columns=["date", "trends"])

    out = df.reset_index()[["date", termo]].rename(columns={termo: "trends"})
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["trends"] = out["trends"].astype(float)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# 4) TEXTO PARA IA — posts do Reddit (.json público, precisa User-Agent)
# --------------------------------------------------------------------------

def fetch_reddit_posts(subreddit: str = "Bitcoin", listing: str = "new",
                       limit: int = 100) -> pd.DataFrame:
    """
    Baixa posts recentes de um subreddit via endpoint .json público.

    IMPORTANTE: sempre enviar header User-Agent, senão o Reddit responde
    429/403. Assuma que só vêm posts RECENTES (não há histórico longo aqui).

    Retorna DataFrame ['date', 'title', 'text', 'subreddit'] ou vazio.
    """
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, params={"limit": limit},
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:
        print(f"[Reddit r/{subreddit}] Falha ao baixar posts: {e}")
        return pd.DataFrame(columns=["date", "title", "text", "subreddit"])

    linhas = []
    for c in children:
        d = c.get("data", {})
        ts = d.get("created_utc")
        if ts is None:
            continue
        linhas.append({
            "date": pd.to_datetime(ts, unit="s"),
            "title": d.get("title", "") or "",
            "text": d.get("selftext", "") or "",
            "subreddit": subreddit,
        })

    if not linhas:
        return pd.DataFrame(columns=["date", "title", "text", "subreddit"])

    return pd.DataFrame(linhas).sort_values("date").reset_index(drop=True)


def fetch_crypto_news(limit: int = 50) -> pd.DataFrame:
    """
    Baixa manchetes recentes de cripto da CryptoCompare (grátis, sem chave).

    Serve de FALLBACK para o texto da IA: o Reddit bloqueia requisições de
    servidores de datacenter (HTTP 429/403), o que esvazia a tabela quando o
    app roda na nuvem (ex.: Streamlit Cloud). A CryptoCompare libera esses
    servidores. As colunas são iguais às do Reddit, para uso intercambiável.

    Retorna DataFrame ['date', 'title', 'text', 'subreddit'] ou vazio.
    (Em 'subreddit' colocamos a fonte da notícia, ex.: "news:coindesk".)
    """
    url = "https://min-api.cryptocompare.com/data/v2/news/"
    try:
        r = requests.get(url, params={"lang": "EN"}, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        artigos = r.json().get("Data", [])
    except Exception as e:
        print(f"[News] Falha ao baixar notícias: {e}")
        return pd.DataFrame(columns=["date", "title", "text", "subreddit"])

    linhas = []
    for a in artigos[:limit]:
        ts = a.get("published_on")
        if ts is None:
            continue
        linhas.append({
            "date": pd.to_datetime(ts, unit="s"),
            "title": a.get("title", "") or "",
            "text": a.get("body", "") or "",
            "subreddit": f"news:{a.get('source', 'cryptocompare')}",
        })

    if not linhas:
        return pd.DataFrame(columns=["date", "title", "text", "subreddit"])

    return pd.DataFrame(linhas).sort_values("date").reset_index(drop=True)


def fetch_textos_para_ia(subreddits=("Bitcoin",), limit: int = 100) -> pd.DataFrame:
    """
    Junta texto para a IA com FALLBACK robusto: tenta o Reddit (posts dos
    subreddits pedidos) e, se vier vazio (ex.: bloqueio na nuvem), cai para
    as notícias da CryptoCompare. Sempre devolve as mesmas colunas.
    """
    frames = []
    for sub in subreddits:
        df = fetch_reddit_posts(sub, "new", limit)
        if not df.empty:
            frames.append(df)

    if frames:
        return pd.concat(frames, ignore_index=True)

    print("[Texto IA] Reddit indisponível — usando notícias (CryptoCompare).")
    return fetch_crypto_news(limit=limit)



# --------------------------------------------------------------------------
# Utilidades de análise
# --------------------------------------------------------------------------

def desvio_da_media(serie: pd.Series) -> pd.Series:
    """
    Converte uma série de humor em 'desvio da média' (centrada em zero).

    Assim o painel de humor fica positivo (verde) quando acima da média
    histórica e negativo (vermelho) quando abaixo — bem mais legível.
    """
    return serie - serie.mean()


def correlacao(a: pd.Series, b: pd.Series) -> float:
    """Correlação de Pearson entre duas séries já alinhadas (ignora NaN)."""
    juntos = pd.concat([a, b], axis=1).dropna()
    if len(juntos) < 3:
        return float("nan")
    return float(juntos.iloc[:, 0].corr(juntos.iloc[:, 1]))


def correlacao_defasada(preco: pd.Series, humor: pd.Series,
                        max_lag: int = 14) -> pd.DataFrame:
    """
    Testa se o HUMOR ANTECIPA o PREÇO usando correlação defasada (lag).

    Para cada lag de 0..max_lag dias, desloca o humor para frente e mede
    a correlação com o preço. Um pico em lag>0 sugere que o humor de hoje
    se relaciona com o preço de daqui a N dias (poder preditivo fraco!).

    Retorna DataFrame ['lag', 'corr'].
    """
    linhas = []
    for lag in range(0, max_lag + 1):
        # humor.shift(lag): humor de N dias atrás alinhado ao preço de hoje
        c = correlacao(preco, humor.shift(lag))
        linhas.append({"lag": lag, "corr": c})
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------
# Gráfico no tema escuro: preço em cima, humor (desvio da média) embaixo
# --------------------------------------------------------------------------

def plot_preco_e_humor(df: pd.DataFrame, col_humor: str, titulo: str,
                       arquivo_png: str, label_humor: str = "Humor",
                       corr: float | None = None) -> str:
    """
    Desenha o gráfico padrão do projeto e salva em PNG.

    df precisa ter: 'date', 'price' e a coluna `col_humor`.
    O painel de humor é mostrado como DESVIO DA MÉDIA (verde acima / vermelho
    abaixo). Retorna o caminho do PNG salvo.
    """
    import matplotlib
    matplotlib.use("Agg")  # backend sem tela (serve em servidor/headless)
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True,
        gridspec_kw={"height_ratios": [2, 1]},
    )

    # --- Painel de cima: preço (linha laranja) ---
    ax1.plot(df["date"], df["price"], color="#f7931a", linewidth=1.6,
             label="BTC (USD)")
    titulo_completo = titulo
    if corr is not None:
        titulo_completo += f"   |   correlação = {corr:.3f}"
    ax1.set_title(titulo_completo, fontsize=13, color="white")
    ax1.set_ylabel("Preço BTC (USD)", color="#f7931a")
    ax1.grid(True, alpha=0.15)
    ax1.legend(loc="upper left", framealpha=0.2)

    # --- Painel de baixo: humor como desvio da média (verde/vermelho) ---
    desvio = desvio_da_media(df[col_humor])
    cores = ["#26a69a" if v >= 0 else "#ef5350" for v in desvio]
    ax2.bar(df["date"], desvio, color=cores, width=1.0)
    ax2.axhline(0, color="white", linewidth=0.6, alpha=0.5)
    ax2.set_ylabel(f"{label_humor}\n(desvio da média)")
    ax2.set_xlabel("Data")
    ax2.grid(True, alpha=0.15)

    fig.tight_layout()
    fig.savefig(arquivo_png, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[Gráfico] PNG salvo em: {arquivo_png}")
    return arquivo_png
