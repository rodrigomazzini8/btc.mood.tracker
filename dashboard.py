# -*- coding: utf-8 -*-
"""
dashboard.py — btc-mood-tracker
================================

Dashboard interativo (Streamlit + Plotly) que une todas as fontes:

  - Preço do BTC (Binance)            -> linha laranja em cima
  - Fear & Greed Index (alternative.me) -> humor (desvio da média) embaixo
  - Google Trends (opcional)          -> 2ª linha de humor, se disponível
  - Reddit + IA (VADER ou FinBERT)    -> tabela de posts classificados

Tudo grátis e sem chave de API. Fontes opcionais (Trends, FinBERT) podem
estar indisponíveis sem quebrar o app.

Rodar:
    streamlit run dashboard.py
"""

import os
import sys

# Importa as funções compartilhadas dos scripts.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import common  # noqa: E402

# --------------------------------------------------------------------------
# Configuração da página
# --------------------------------------------------------------------------
st.set_page_config(page_title="BTC Mood Tracker", page_icon="📈", layout="wide")

# Paleta do tema escuro.
LARANJA = "#f7931a"
VERDE = "#26a69a"
VERMELHO = "#ef5350"


# --------------------------------------------------------------------------
# Carregadores com cache (não rebaixam dados a cada interação)
# --------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Baixando preço do BTC...")
def carregar_preco(dias: int) -> pd.DataFrame:
    return common.fetch_btc_binance(dias=dias)


@st.cache_data(ttl=3600, show_spinner="Baixando Fear & Greed...")
def carregar_fng() -> pd.DataFrame:
    return common.fetch_fear_greed(limit=0)


@st.cache_data(ttl=3600, show_spinner="Consultando Google Trends...")
def carregar_trends() -> pd.DataFrame:
    # Fonte opcional: se falhar, devolve vazio (não quebra o app).
    return common.fetch_google_trends(termo="Bitcoin", timeframe="today 5-y")


@st.cache_data(ttl=900, show_spinner="Buscando posts do Reddit...")
def carregar_reddit(subreddits: tuple) -> pd.DataFrame:
    frames = [common.fetch_reddit_posts(sub, "new", 100) for sub in subreddits]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["date", "title", "text", "subreddit"])
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=900)
def sentimento_vader(posts: pd.DataFrame) -> pd.DataFrame:
    """Classifica posts com VADER (leve, sempre disponível)."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except Exception:
        return posts.assign(nota=float("nan"), modelo="VADER (indisponível)")
    an = SentimentIntensityAnalyzer()
    notas = []
    for _, r in posts.iterrows():
        txt = f"{r['title']} {r['text']}".strip()
        notas.append(an.polarity_scores(txt)["compound"] if txt else 0.0)
    return posts.assign(nota=notas, modelo="VADER")


@st.cache_resource(show_spinner="Carregando FinBERT (pode baixar o modelo)...")
def carregar_pipeline_finbert():
    """Carrega o FinBERT uma única vez (cache_resource). None se falhar."""
    try:
        from transformers import (AutoTokenizer,
                                  AutoModelForSequenceClassification,
                                  TextClassificationPipeline)
        nome = "ProsusAI/finbert"
        tok = AutoTokenizer.from_pretrained(nome)
        modelo = AutoModelForSequenceClassification.from_pretrained(nome)
        return TextClassificationPipeline(model=modelo, tokenizer=tok,
                                          top_k=None, truncation=True)
    except Exception as e:
        print(f"[FinBERT] indisponível: {e}")
        return None


def sentimento_finbert(posts: pd.DataFrame) -> pd.DataFrame:
    """Classifica posts com FinBERT; cai pro VADER se indisponível."""
    pipe = carregar_pipeline_finbert()
    if pipe is None:
        st.warning("FinBERT indisponível — usando VADER no lugar.")
        return sentimento_vader(posts)

    pesos = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
    notas = []
    for _, r in posts.iterrows():
        txt = f"{r['title']}. {r['text']}".strip()[:512]
        try:
            res = pipe(txt)
            scores = res[0] if isinstance(res[0], list) else res
            notas.append(sum(pesos.get(s["label"].lower(), 0) * s["score"]
                             for s in scores))
        except Exception:
            notas.append(0.0)
    return posts.assign(nota=notas, modelo="FinBERT")


# --------------------------------------------------------------------------
# Sidebar: filtros
# --------------------------------------------------------------------------
st.sidebar.title("⚙️ Filtros")

periodo = st.sidebar.select_slider(
    "Período (dias)", options=[90, 180, 365, 730, 1095, 1500], value=730)

subs_disponiveis = ["Bitcoin", "CryptoCurrency", "btc", "CryptoMarkets"]
subs_escolhidos = st.sidebar.multiselect(
    "Subreddits (texto p/ IA)", subs_disponiveis, default=["Bitcoin"])

usar_finbert = st.sidebar.toggle(
    "Usar FinBERT (IA avançada)", value=False,
    help="Desligado = VADER (rápido). Ligado = FinBERT (baixa modelo ~440MB).")

mostrar_trends = st.sidebar.checkbox("Sobrepor Google Trends (se disponível)", value=True)

st.sidebar.markdown("---")
st.sidebar.caption("Fontes grátis, sem chave de API.\n\n⚠️ Não é recomendação financeira.")


# --------------------------------------------------------------------------
# Corpo principal
# --------------------------------------------------------------------------
st.title("📈 BTC Mood Tracker")
st.caption("Cruzando o preço do Bitcoin com o humor do mercado — só com dados grátis.")

# Dados base (preço + humor).
preco = carregar_preco(periodo)
fng = carregar_fng()

if preco.empty or fng.empty:
    st.error("Não foi possível carregar preço ou Fear & Greed agora. "
             "Tente novamente em instantes (limite de rede/API).")
    st.stop()

df = preco.merge(fng, on="date", how="inner").sort_values("date")
# Recorta ao período pedido (Fear&Greed cobre desde 2018).
corte = df["date"].max() - pd.Timedelta(days=periodo)
df = df[df["date"] >= corte].reset_index(drop=True)

corr = common.correlacao(df["price"], df["fng"])

# --- Métricas no topo ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Preço BTC (último)", f"${df['price'].iloc[-1]:,.0f}")
fng_atual = df["fng"].iloc[-1]
rotulo = ("Ganância" if fng_atual >= 55 else "Medo" if fng_atual <= 45 else "Neutro")
c2.metric("Fear & Greed", f"{fng_atual:.0f}/100", rotulo)
c3.metric("Correlação (preço×humor)", f"{corr:.3f}")
var = (df["price"].iloc[-1] / df["price"].iloc[0] - 1) * 100 if len(df) > 1 else 0
c4.metric("Variação no período", f"{var:+.1f}%")

# --- Google Trends opcional ---
trends = carregar_trends() if mostrar_trends else pd.DataFrame()
tem_trends = not trends.empty
if tem_trends:
    trends = trends.set_index("date").resample("D").interpolate().reset_index()
    df = df.merge(trends, on="date", how="left")
    df["trends"] = df["trends"].interpolate()
elif mostrar_trends:
    st.info("Google Trends indisponível agora (rate limit). Mostrando sem ele.")

# --------------------------------------------------------------------------
# Gráfico Plotly: preço em cima, humor (desvio da média) embaixo
# --------------------------------------------------------------------------
fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                    vertical_spacing=0.06, row_heights=[0.62, 0.38],
                    subplot_titles=("Preço BTC (USD)", "Humor — desvio da média"))

# Painel de cima: preço.
fig.add_trace(go.Scatter(x=df["date"], y=df["price"], name="BTC (USD)",
                         line=dict(color=LARANJA, width=1.8)), row=1, col=1)

# Painel de baixo: Fear & Greed como desvio da média (verde/vermelho).
dev = common.desvio_da_media(df["fng"])
fig.add_trace(go.Bar(x=df["date"], y=dev, name="Fear&Greed (desvio)",
                     marker_color=[VERDE if v >= 0 else VERMELHO for v in dev]),
              row=2, col=1)

# Sobreposição opcional de Trends (linha) no painel de humor.
if tem_trends:
    dev_tr = common.desvio_da_media(df["trends"])
    fig.add_trace(go.Scatter(x=df["date"], y=dev_tr, name="Google Trends (desvio)",
                             line=dict(color="#42a5f5", width=1.2)), row=2, col=1)

fig.update_layout(template="plotly_dark", height=620,
                  margin=dict(l=10, r=10, t=40, b=10),
                  legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------
# Tabela de posts classificados pela IA
# --------------------------------------------------------------------------
st.subheader("🧠 Posts do Reddit classificados pela IA")

if not subs_escolhidos:
    st.info("Escolha ao menos um subreddit na barra lateral para ver os posts.")
else:
    posts = carregar_reddit(tuple(subs_escolhidos))
    if posts.empty:
        st.warning("Sem posts do Reddit agora (provável rate limit). Tente depois.")
    else:
        classificados = (sentimento_finbert(posts) if usar_finbert
                         else sentimento_vader(posts))
        modelo_usado = classificados["modelo"].iloc[0]

        # Rótulo legível a partir da nota.
        def rotular(n):
            return "🟢 positivo" if n > 0.15 else "🔴 negativo" if n < -0.15 else "⚪ neutro"

        tabela = classificados.assign(sentimento=classificados["nota"].map(rotular))
        st.caption(f"Modelo: **{modelo_usado}**  |  "
                   f"nota média: **{classificados['nota'].mean():.3f}**  |  "
                   f"{len(tabela)} posts")
        st.dataframe(
            tabela.sort_values("date", ascending=False)[
                ["date", "subreddit", "title", "sentimento", "nota"]],
            use_container_width=True, hide_index=True,
            column_config={
                "date": "Data",
                "subreddit": "Sub",
                "title": "Título",
                "sentimento": "IA",
                "nota": st.column_config.NumberColumn("Nota", format="%.3f"),
            })

st.markdown("---")
st.caption("⚠️ Conteúdo educativo. **Não é recomendação financeira.** "
           "Correlação não implica causalidade.")
