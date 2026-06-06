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


@st.cache_data(ttl=86400, show_spinner="Buscando indicadores on-chain...")
def carregar_onchain_series() -> dict:
    """
    Busca a SÉRIE de cada métrica on-chain UMA vez por dia (cache em memória
    do Streamlit, que sobrevive a reruns — diferente do cache em disco, que é
    apagado no Streamlit Cloud). Devolve {metrica: [(date_iso, valor), ...]}.

    Como @st.cache_data só guarda o RETORNO em caso de sucesso, e aqui só
    chamamos a API 1x/dia, o consumo da cota (15 req/dia) cai drasticamente.
    """
    if not term.tem_chave_onchain():
        return {}
    out = {}
    for m in term.BGEO_ENDPOINTS:
        s = term.fetch_onchain_serie(m)  # 1 requisição traz a série inteira
        if not s.empty:
            out[m] = [(d.strftime("%Y-%m-%d"), float(v))
                      for d, v in zip(s["date"], s["valor"])]
    return out


def _onchain_series_df(series_dict: dict) -> dict:
    """Converte o dict serializável em {metrica: DataFrame['date','valor']}."""
    res = {}
    for m, pares in (series_dict or {}).items():
        if pares:
            df = pd.DataFrame(pares, columns=["date", "valor"])
            df["date"] = pd.to_datetime(df["date"])
            res[m] = df
    return res


@st.cache_data(ttl=3600, show_spinner="Calculando o termômetro (indicadores)...")
def carregar_snapshot_termometro(periodo: int, fng_atual: float,
                                 onchain_series: tuple) -> pd.DataFrame:
    # Usa histórico longo para os indicadores de média móvel (200d/200w).
    preco_longo = common.fetch_btc_price(dias=1500)
    series = _onchain_series_df(dict(onchain_series))
    # Último valor de cada série on-chain (já cacheada pelo Streamlit).
    valores_oc = {m: float(df["valor"].dropna().iloc[-1])
                  for m, df in series.items() if not df["valor"].dropna().empty}
    return term.montar_snapshot(preco_longo, fng_atual=fng_atual,
                                valores_onchain=valores_oc if valores_oc else None)


@st.cache_data(ttl=3600, show_spinner="Recalculando score histórico...")
def carregar_score_historico(periodo: int, selecionados: tuple,
                             pesos_itens: tuple, onchain_series: tuple) -> pd.DataFrame:
    preco_longo = common.fetch_btc_price(dias=1500)
    fng_full = common.fetch_fear_greed(limit=0)
    sel = list(selecionados) if selecionados else None
    pesos = dict(pesos_itens) if pesos_itens else None
    series_oc = _onchain_series_df(dict(onchain_series))  # já buscadas (cache)
    return term.serie_score_historico(preco_longo, fng_full, selecionados=sel,
                                      pesos=pesos, series_onchain=series_oc)


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

# Header de destaque (preço + variação 24h + sinal). Placeholder preenchido
# assim que o score do termômetro estiver calculado.
header_box = st.empty()

# Cores por sinal (usadas no header, gauge e tabela).
COR_SINAL = {
    "COMPRA FORTE": "#1b7f4d", "COMPRA": "#26a69a", "NEUTRO": "#8a8f98",
    "VENDA": "#ef5350", "VENDA FORTE": "#b71c1c", "—": "#444",
}

# Dados base (preço + humor).
preco = carregar_preco(periodo)
fng = carregar_fng()
if preco.empty or fng.empty:
    st.error("Não foi possível carregar preço ou Fear & Greed agora. "
             "Tente novamente em instantes (limite de rede/API).")
    st.stop()

df = preco.merge(fng, on="date", how="inner").sort_values("date")
corte = df["date"].max() - pd.Timedelta(days=periodo)
df = df[df["date"] >= corte].reset_index(drop=True)
fng_atual = df["fng"].iloc[-1]
corr = common.correlacao(df["price"], df["fng"])

# On-chain buscado 1x/dia (cache em memória) e reaproveitado em tudo.
onchain_series = carregar_onchain_series()
onchain_tuple = tuple(sorted(onchain_series.items()))
snapshot = carregar_snapshot_termometro(periodo, float(fng_atual), onchain_tuple)

# ==========================================================================
# CONFIGURAÇÃO DO TERMÔMETRO (escondida num expander para o app ficar enxuto)
# ==========================================================================
with st.expander("⚙️ Configurar indicadores do termômetro"):
    if not term.tem_chave_onchain():
        st.caption("On-chain (MVRV, SOPR, NUPL, Puell...) aparecem ao definir a "
                   "chave grátis `BGEO_API_KEY` (api.bgeometrics.com).")
    modo_pesos = st.toggle("⚖️ Ajustar pesos por indicador", value=False,
                           help="Desligado = média simples.")

    disp = snapshot[snapshot["ok"]]
    selecionados, pesos = [], {}

    def _render_indicador(col, row):
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

    _explic = getattr(term, "EXPLICACOES", {})
    st.markdown("**O que significa cada indicador:**")
    for r in snapshot.itertuples():
        exp = _explic.get(r.chave, "")
        if exp:
            st.caption(f"**{r.indicador}** — {exp}")

# Score consolidado (ponderado se o modo avançado estiver ligado).
pesos_ativos = pesos if modo_pesos else None
cons = term.consolidar(snapshot, selecionados or None, pesos=pesos_ativos)
sinal_cons = term.score_para_sinal(cons) if cons == cons else "—"
n_usados = len([s for s in selecionados if s in set(disp["chave"])])

# --- Preenche o HEADER (preço grande + variação 24h + sinal) ---
preco_atual = float(preco["price"].iloc[-1])
var24 = ((preco["price"].iloc[-1] / preco["price"].iloc[-2] - 1) * 100
         if len(preco) > 1 else 0.0)
cor_var = VERDE if var24 >= 0 else VERMELHO
with header_box.container():
    h1, h2 = st.columns([3, 2])
    with h1:
        st.markdown(
            f"<div style='font-size:13px;opacity:.7'>BITCOIN · BTC/USD</div>"
            f"<div style='font-size:40px;font-weight:800;line-height:1.1'>"
            f"${preco_atual:,.0f}</div>"
            f"<div style='font-size:16px;color:{cor_var}'>{var24:+.2f}% (24h)</div>",
            unsafe_allow_html=True)
    with h2:
        st.markdown(
            f"<div style='background:{COR_SINAL.get(sinal_cons, '#444')};"
            f"padding:14px;border-radius:12px;text-align:center;margin-top:6px'>"
            f"<div style='font-size:12px;opacity:.85'>TERMÔMETRO</div>"
            f"<div style='font-size:24px;font-weight:700'>{sinal_cons}</div>"
            f"<div style='font-size:14px'>score {cons:.2f} · {n_usados} ind.</div>"
            f"</div>", unsafe_allow_html=True)

# Alerta de zona extrema.
if sinal_cons == "COMPRA FORTE":
    st.success(f"🟢 **COMPRA FORTE** (score {cons:.2f}). Zona historicamente de "
               "acumulação. *Não é recomendação financeira.*")
elif sinal_cons == "VENDA FORTE":
    st.error(f"🔴 **VENDA FORTE** (score {cons:.2f}). Zona historicamente "
             "esticada. *Não é recomendação financeira.*")

# Log diário do score (histórico próprio).
try:
    term.registrar_log_diario(preco_atual, float(fng_atual), cons, sinal_cons)
except Exception:
    pass

# ==========================================================================
# ABAS — deixam o app enxuto: cada assunto na sua aba.
# ==========================================================================
aba_term, aba_preco, aba_bt, aba_ia = st.tabs(
    ["🌡️ Termômetro", "📊 Preço & Humor", "🧪 Backtest", "🧠 IA"])

# --------------------------------------------------------------------------
# ABA 1 — TERMÔMETRO (gauge + tabela + resumo + histórico do score)
# --------------------------------------------------------------------------
with aba_term:
    g1, g2 = st.columns([1, 1])
    with g1:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=cons if cons == cons else 0,
            number={"valueformat": ".2f", "font": {"size": 28}},
            title={"text": f"{sinal_cons}"},
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
        gauge.update_layout(template="plotly_dark", height=240,
                            margin=dict(l=20, r=20, t=40, b=0))
        st.plotly_chart(gauge, use_container_width=True)
    with g2:
        ok_scores = snapshot[snapshot["ok"]]["score"].dropna()
        st.metric("🟢 Compra", int((ok_scores > 0).sum()))
        st.metric("⚪ Neutro", int((ok_scores == 0).sum()))
        st.metric("🔴 Venda", int((ok_scores < 0).sum()))

    # Tabela detalhada (Indicador | Tipo | Valor | Sinal | Score).
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

    def _cor_sinal(val):
        c = COR_SINAL.get(val, "")
        return f"background-color:{c};color:white;font-weight:600" if c else ""

    styler = (tab_show.style
              .map(_cor_sinal, subset=["Sinal"])
              .format({"Score": lambda v: "—" if pd.isna(v) else f"{int(v):+d}"}))
    st.dataframe(styler, use_container_width=True, hide_index=True)
    st.caption("Score por indicador (−2 a +2); o consolidado é a média dos "
               "selecionados. **Não é recomendação financeira.**")

    # Gráfico do score histórico × preço.
    oc_sel = tuple((m, v) for m, v in onchain_tuple if m in selecionados)
    hist = carregar_score_historico(
        periodo, tuple(sorted(selecionados)),
        tuple(sorted(pesos.items())) if (modo_pesos and pesos) else (), oc_sel)
    if not hist.empty:
        corte_h = hist["date"].max() - pd.Timedelta(days=periodo)
        hist = hist[hist["date"] >= corte_h]
        figt = make_subplots(specs=[[{"secondary_y": True}]])
        figt.add_trace(go.Scatter(x=hist["date"], y=hist["price"], name="BTC (USD)",
                                  line=dict(color=LARANJA, width=1.6)), secondary_y=False)
        figt.add_trace(go.Scatter(x=hist["date"], y=hist["score"], name="Score",
                                  line=dict(color="#42a5f5", width=1.4),
                                  fill="tozeroy", fillcolor="rgba(66,165,245,0.15)"),
                       secondary_y=True)
        figt.update_layout(template="plotly_dark", height=360,
                           margin=dict(l=10, r=10, t=30, b=10),
                           title="Histórico do Score × Preço",
                           legend=dict(orientation="h", y=1.12))
        figt.update_yaxes(title_text="Preço (USD)", secondary_y=False)
        figt.update_yaxes(title_text="Score", range=[-2.2, 2.2], secondary_y=True)
        st.plotly_chart(figt, use_container_width=True)

    # Histórico próprio (log diário), se já houver dias suficientes.
    log = term.ler_log_diario()
    if len(log) >= 2:
        with st.expander(f"📅 Meu histórico de sinais ({len(log)} dias)"):
            figl = make_subplots(specs=[[{"secondary_y": True}]])
            figl.add_trace(go.Scatter(x=log["date"], y=log["price"], name="BTC",
                                      line=dict(color=LARANJA, width=1.6)), secondary_y=False)
            figl.add_trace(go.Scatter(x=log["date"], y=log["score"], name="Score",
                                      mode="lines+markers",
                                      line=dict(color="#42a5f5", width=1.4)), secondary_y=True)
            figl.update_layout(template="plotly_dark", height=280,
                               margin=dict(l=10, r=10, t=10, b=10),
                               legend=dict(orientation="h", y=1.15))
            figl.update_yaxes(title_text="Preço", secondary_y=False)
            figl.update_yaxes(title_text="Score", range=[-2.2, 2.2], secondary_y=True)
            st.plotly_chart(figl, use_container_width=True)

# --------------------------------------------------------------------------
# ABA 2 — PREÇO & HUMOR (Fear & Greed + Google Trends)
# --------------------------------------------------------------------------
with aba_preco:
    m1, m2, m3 = st.columns(3)
    m1.metric("Fear & Greed", f"{fng_atual:.0f}/100",
              "Ganância" if fng_atual >= 55 else "Medo" if fng_atual <= 45 else "Neutro")
    m2.metric("Correlação (preço×humor)", f"{corr:.3f}")
    var = (df["price"].iloc[-1] / df["price"].iloc[0] - 1) * 100 if len(df) > 1 else 0
    m3.metric("Variação no período", f"{var:+.1f}%")

    # Google Trends opcional.
    trends = carregar_trends() if mostrar_trends else pd.DataFrame()
    tem_trends = not trends.empty
    if tem_trends:
        trends = trends.set_index("date").resample("D").interpolate().reset_index()
        df = df.merge(trends, on="date", how="left")
        df["trends"] = df["trends"].interpolate()
    elif mostrar_trends:
        st.caption("Google Trends indisponível agora (rate limit).")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        row_heights=[0.62, 0.38],
                        subplot_titles=("Preço BTC (USD)", "Humor — desvio da média"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["price"], name="BTC (USD)",
                             line=dict(color=LARANJA, width=1.8)), row=1, col=1)
    dev = common.desvio_da_media(df["fng"])
    fig.add_trace(go.Bar(x=df["date"], y=dev, name="Fear&Greed (desvio)",
                         marker_color=[VERDE if v >= 0 else VERMELHO for v in dev]),
                  row=2, col=1)
    if tem_trends:
        dev_tr = common.desvio_da_media(df["trends"])
        fig.add_trace(go.Scatter(x=df["date"], y=dev_tr, name="Google Trends (desvio)",
                                 line=dict(color="#42a5f5", width=1.2)), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=560,
                      margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------
# ABA 3 — BACKTEST
# --------------------------------------------------------------------------
with aba_bt:
    st.caption("Estratégia LONG/CAIXA guiada pelo score (decisão de ontem no "
               "retorno de hoje, sem custos) vs comprar e segurar. "
               "**Não é recomendação financeira.**")
    bc1, bc2 = st.columns(2)
    th_entrar = bc1.slider("Entrar quando score ≥", 0.0, 2.0, 0.5, 0.25)
    th_sair = bc2.slider("Sair quando score ≤", -2.0, 0.0, -0.5, 0.25)

    bt = term.backtest_score(hist, entrar=th_entrar, sair=th_sair) \
        if not hist.empty else {}
    if not bt:
        st.info("Sem histórico suficiente para o backtest neste período.")
    else:
        e, h = bt["estrategia"], bt["hold"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Estratégia", f"{e['ret_total']*100:+.0f}%", f"CAGR {e['cagr']*100:.0f}%")
        m2.metric("Buy & Hold", f"{h['ret_total']*100:+.0f}%", f"CAGR {h['cagr']*100:.0f}%")
        m3.metric("Drawdown", f"{e['dd_max']*100:.0f}%",
                  f"hold {h['dd_max']*100:.0f}%", delta_color="off")
        m4.metric("Tempo investido", f"{e.get('exposicao', 0)*100:.0f}%")

        wr = e.get("win_rate", float("nan"))
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Sharpe", f"{e['sharpe']:.2f}", f"hold {h['sharpe']:.2f}", delta_color="off")
        s2.metric("Operações", f"{e.get('operacoes', 0)}")
        s3.metric("Win rate", "—" if wr != wr else f"{wr*100:.0f}%")
        venceu = e["ret_total"] > h["ret_total"]
        s4.metric("vs Hold", "ganhou" if venceu else "perdeu",
                  f"{(e['ret_total']-h['ret_total'])*100:+.0f} p.p.",
                  delta_color="normal" if venceu else "inverse")

        curva = bt["curva"]
        figb = go.Figure()
        figb.add_trace(go.Scatter(x=curva["date"], y=curva["cap_estrategia"],
                                  name="Estratégia (score)", line=dict(color=VERDE, width=1.8)))
        figb.add_trace(go.Scatter(x=curva["date"], y=curva["cap_hold"],
                                  name="Buy & Hold", line=dict(color=LARANJA, width=1.6)))
        figb.update_layout(template="plotly_dark", height=340,
                           margin=dict(l=10, r=10, t=30, b=10),
                           title="Capital acumulado (1 = início)",
                           legend=dict(orientation="h", y=1.12),
                           yaxis_title="Múltiplo do capital")
        st.plotly_chart(figb, use_container_width=True)
        st.caption("⚠️ Simplificado (sem taxas/impostos) — não prevê o futuro.")

# --------------------------------------------------------------------------
# ABA 4 — IA (Reddit/notícias classificados)
# --------------------------------------------------------------------------
with aba_ia:
    st.caption("Tenta o Reddit; na nuvem usa notícias de cripto (CryptoCompare) "
               "como fallback. Classificação por VADER ou FinBERT (toggle na barra).")
    if not subs_escolhidos:
        st.info("Escolha ao menos um subreddit na barra lateral.")
    else:
        posts = carregar_reddit(tuple(subs_escolhidos))
        if posts.empty:
            st.warning("Sem texto disponível agora (rate limit). Tente depois.")
        else:
            classificados = (sentimento_finbert(posts) if usar_finbert
                             else sentimento_vader(posts))
            modelo_usado = classificados["modelo"].iloc[0]

            def rotular(n):
                return "🟢 positivo" if n > 0.15 else "🔴 negativo" if n < -0.15 else "⚪ neutro"

            tabela = classificados.assign(sentimento=classificados["nota"].map(rotular))
            st.caption(f"Modelo: **{modelo_usado}**  |  "
                       f"nota média: **{classificados['nota'].mean():.3f}**  |  "
                       f"{len(tabela)} textos")
            st.dataframe(
                tabela.sort_values("date", ascending=False)[
                    ["date", "subreddit", "title", "sentimento", "nota"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "date": "Data", "subreddit": "Fonte", "title": "Título",
                    "sentimento": "IA",
                    "nota": st.column_config.NumberColumn("Nota", format="%.3f"),
                })

st.markdown("---")
st.caption("⚠️ Conteúdo educativo. **Não é recomendação financeira.** "
           "Correlação não implica causalidade.")
