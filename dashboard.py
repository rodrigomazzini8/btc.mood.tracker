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
import termometro as term  # noqa: E402  (score consolidado estilo "termômetro")

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
    return common.fetch_btc_price(dias=dias)


@st.cache_data(ttl=3600, show_spinner="Baixando Fear & Greed...")
def carregar_fng() -> pd.DataFrame:
    return common.fetch_fear_greed(limit=0)


@st.cache_data(ttl=3600, show_spinner="Consultando Google Trends...")
def carregar_trends() -> pd.DataFrame:
    # Fonte opcional: se falhar, devolve vazio (não quebra o app).
    return common.fetch_google_trends(termo="Bitcoin", timeframe="today 5-y")


@st.cache_data(ttl=3600, show_spinner="Calculando o termômetro (indicadores)...")
def carregar_snapshot_termometro(periodo: int, fng_atual: float) -> pd.DataFrame:
    # Usa histórico longo para os indicadores de média móvel (200d/200w).
    preco_longo = common.fetch_btc_price(dias=1500)
    return term.montar_snapshot(preco_longo, fng_atual=fng_atual, incluir_onchain=True)


@st.cache_data(ttl=3600, show_spinner="Recalculando score histórico...")
def carregar_score_historico(periodo: int, selecionados: tuple,
                             incluir_onchain: bool, pesos_itens: tuple) -> pd.DataFrame:
    preco_longo = common.fetch_btc_price(dias=1500)
    fng_full = common.fetch_fear_greed(limit=0)
    sel = list(selecionados) if selecionados else None
    pesos = dict(pesos_itens) if pesos_itens else None
    return term.serie_score_historico(preco_longo, fng_full, selecionados=sel,
                                      incluir_onchain=incluir_onchain, pesos=pesos)


@st.cache_data(ttl=900, show_spinner="Buscando texto para a IA (Reddit/notícias)...")
def carregar_reddit(subreddits: tuple) -> pd.DataFrame:
    # Tenta o Reddit; se vier vazio (ex.: bloqueio na nuvem), cai para as
    # notícias da CryptoCompare. Assim a tabela da IA sempre tem conteúdo.
    return common.fetch_textos_para_ia(subreddits, limit=100)


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
# 🌡️ TERMÔMETRO DO BITCOIN — score consolidado de compra/venda
# --------------------------------------------------------------------------
st.markdown("---")
st.subheader("🌡️ Termômetro do Bitcoin")
st.caption("Sinais de compra/venda combinando vários indicadores num score de "
           "−2 (venda forte) a +2 (compra forte). **Não é recomendação financeira.**")

snapshot = carregar_snapshot_termometro(periodo, float(fng_atual))

# Cores por sinal (para os cards e a tabela).
COR_SINAL = {
    "COMPRA FORTE": "#1b7f4d", "COMPRA": "#26a69a", "NEUTRO": "#8a8f98",
    "VENDA": "#ef5350", "VENDA FORTE": "#b71c1c", "—": "#444",
}

# Aviso se on-chain estiver indisponível (sem chave).
if not term.tem_chave_onchain():
    st.info("Indicadores on-chain (MVRV, SOPR, MVRV Z-Score, NUPL, Puell, "
            "Reserve Risk) ficam disponíveis ao definir a chave grátis "
            "`BGEO_API_KEY` (api.bgeometrics.com). Sem ela, o termômetro usa "
            "só os indicadores grátis.")

# Modo avançado: permite dar PESO diferente a cada indicador (senão, média
# simples). Fica num toggle para não poluir a interface básica.
modo_pesos = st.toggle("⚖️ Ajustar pesos por indicador (avançado)", value=False,
                       help="Dê mais ou menos importância a cada indicador no "
                            "score consolidado. Desligado = média simples.")

# Checkboxes: quais indicadores entram no consolidado (default: todos os ok).
# Separados em GRÁTIS e ON-CHAIN para ficar mais organizado.
disp = snapshot[snapshot["ok"]]
st.markdown("**Escolha os indicadores usados no cálculo:**")
selecionados = []
pesos = {}

def _render_indicador(col, row):
    """Checkbox (+ slider de peso no modo avançado) de um indicador."""
    with col:
        marcado = st.checkbox(row.indicador, value=True, key=f"chk_{row.chave}")
        if marcado and modo_pesos:
            pesos[row.chave] = st.slider(
                "peso", 0.0, 3.0, 1.0, 0.5, key=f"peso_{row.chave}",
                label_visibility="collapsed")
    if marcado:
        selecionados.append(row.chave)

gratis = disp[~disp["onchain"]]
onchain = disp[disp["onchain"]]

if not gratis.empty:
    st.caption("Grátis (calculados do preço)")
    cols_g = st.columns(max(1, len(gratis)))
    for i, row in enumerate(gratis.itertuples()):
        _render_indicador(cols_g[i], row)

if not onchain.empty:
    st.caption("On-chain (BGeometrics)")
    cols_o = st.columns(min(4, len(onchain)))
    for i, row in enumerate(onchain.itertuples()):
        _render_indicador(cols_o[i % len(cols_o)], row)

# Explicação didática de cada indicador.
with st.expander("ℹ️ O que significa cada indicador?"):
    for r in snapshot.itertuples():
        exp = term.EXPLICACOES.get(r.chave, "")
        if exp:
            st.markdown(f"**{r.indicador}** — {exp}")

# Score consolidado dos selecionados (ponderado se o modo avançado estiver on).
pesos_ativos = pesos if modo_pesos else None
cons = term.consolidar(snapshot, selecionados or None, pesos=pesos_ativos)
sinal_cons = term.score_para_sinal(cons) if cons == cons else "—"
n_usados = len([s for s in selecionados if s in set(disp["chave"])])

# Card grande do sinal consolidado + medidor (gauge) visual.
cc1, cc2 = st.columns([1, 1])
with cc1:
    cor = COR_SINAL.get(sinal_cons, "#444")
    st.markdown(
        f"<div style='background:{cor};padding:18px;border-radius:12px;text-align:center'>"
        f"<div style='font-size:13px;opacity:.85'>SINAL CONSOLIDADO</div>"
        f"<div style='font-size:30px;font-weight:700'>{sinal_cons}</div>"
        f"<div style='font-size:15px'>score: {cons:.2f}  ·  {n_usados} indicadores</div>"
        f"</div>", unsafe_allow_html=True)
    st.caption("Cada indicador gera um score de −2 a +2; o consolidado é a "
               "média dos selecionados. Valores 'baratos' puxam para COMPRA; "
               "'esticados' para VENDA.")
with cc2:
    # Medidor (gauge) de ponteiro do score, de -2 a +2, com zonas coloridas.
    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=cons if cons == cons else 0,
        number={"valueformat": ".2f", "font": {"size": 28}},
        gauge={
            "axis": {"range": [-2, 2], "tickvals": [-2, -1, 0, 1, 2]},
            "bar": {"color": "rgba(255,255,255,0.85)", "thickness": 0.18},
            "steps": [
                {"range": [-2, -1.5], "color": "#b71c1c"},
                {"range": [-1.5, -0.5], "color": "#ef5350"},
                {"range": [-0.5, 0.5], "color": "#8a8f98"},
                {"range": [0.5, 1.5], "color": "#26a69a"},
                {"range": [1.5, 2], "color": "#1b7f4d"},
            ],
            "threshold": {"line": {"color": "white", "width": 3},
                          "value": cons if cons == cons else 0},
        }))
    gauge.update_layout(template="plotly_dark", height=210,
                        margin=dict(l=20, r=20, t=10, b=0))
    st.plotly_chart(gauge, use_container_width=True)

# Tabela detalhada (Indicador | Valor | Sinal | Score), estilo letabuild.
def _fmt_valor(chave, valor, ok):
    if not ok:
        return "indisponível"
    if chave == "fng":
        return f"{valor:.0f}"
    if chave == "rsi_mensal":
        return f"{valor:.1f}"
    return f"{valor:.3f}"

tab = snapshot.copy()
tab["Valor"] = [_fmt_valor(r.chave, r.valor, r.ok) for r in snapshot.itertuples()]
tab["Tipo"] = tab["onchain"].map({True: "on-chain", False: "grátis"})
tab_show = tab.rename(columns={"indicador": "Indicador", "sinal": "Sinal",
                               "score": "Score"})[
    ["Indicador", "Tipo", "Valor", "Sinal", "Score"]]

# Colore a coluna "Sinal" com a cor do respectivo sinal (estilo letabuild).
def _cor_sinal(val):
    c = COR_SINAL.get(val, "")
    return f"background-color:{c};color:white;font-weight:600" if c else ""

styler = (tab_show.style
          .map(_cor_sinal, subset=["Sinal"])
          .format({"Score": lambda v: "—" if pd.isna(v) else f"{int(v):+d}"}))
st.dataframe(styler, use_container_width=True, hide_index=True)

# Resumo: quantos indicadores em cada direção (entre os disponíveis).
ok_scores = snapshot[snapshot["ok"]]["score"].dropna()
n_compra = int((ok_scores > 0).sum())
n_venda = int((ok_scores < 0).sum())
n_neutro = int((ok_scores == 0).sum())
rc1, rc2, rc3 = st.columns(3)
rc1.metric("🟢 Compra", n_compra)
rc2.metric("⚪ Neutro", n_neutro)
rc3.metric("🔴 Venda", n_venda)

# Gráfico: score histórico (área) sobreposto ao preço. Agora inclui a série
# histórica dos on-chain selecionados (via cache) e respeita os pesos.
usa_onchain_hist = any(s in term.ONCHAIN for s in selecionados)
hist = carregar_score_historico(
    periodo, tuple(sorted(selecionados)), usa_onchain_hist,
    tuple(sorted(pesos.items())) if (modo_pesos and pesos) else ())
if not hist.empty:
    corte_h = hist["date"].max() - pd.Timedelta(days=periodo)
    hist = hist[hist["date"] >= corte_h]
    figt = make_subplots(specs=[[{"secondary_y": True}]])
    figt.add_trace(go.Scatter(x=hist["date"], y=hist["price"], name="BTC (USD)",
                              line=dict(color=LARANJA, width=1.6)), secondary_y=False)
    figt.add_trace(go.Scatter(x=hist["date"], y=hist["score"], name="Score consolidado",
                              line=dict(color="#42a5f5", width=1.4),
                              fill="tozeroy", fillcolor="rgba(66,165,245,0.15)"),
                   secondary_y=True)
    figt.update_layout(template="plotly_dark", height=420,
                       margin=dict(l=10, r=10, t=30, b=10),
                       title="Histórico do Score × Preço do Bitcoin",
                       legend=dict(orientation="h", y=1.1))
    figt.update_yaxes(title_text="Preço (USD)", secondary_y=False)
    figt.update_yaxes(title_text="Score (−2 a +2)", range=[-2.2, 2.2], secondary_y=True)
    st.plotly_chart(figt, use_container_width=True)
    st.caption("Score consolidado recalculado ao longo do tempo, sobre o preço. "
               "Inclui os indicadores selecionados (grátis e, se houver chave, "
               "também os on-chain) e respeita os pesos do modo avançado.")

# --------------------------------------------------------------------------
# Tabela de posts classificados pela IA
# --------------------------------------------------------------------------
st.markdown("---")
st.subheader("🧠 Textos (Reddit ou notícias) classificados pela IA")
st.caption("Tenta o Reddit; na nuvem ele costuma bloquear datacenters, então "
           "usa notícias de cripto (CryptoCompare) como fallback.")

if not subs_escolhidos:
    st.info("Escolha ao menos um subreddit na barra lateral para ver os posts.")
else:
    posts = carregar_reddit(tuple(subs_escolhidos))
    if posts.empty:
        st.warning("Sem texto disponível agora (Reddit e notícias indisponíveis "
                   "ou com rate limit). Tente novamente em instantes.")
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
