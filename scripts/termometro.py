# -*- coding: utf-8 -*-
"""
termometro.py — "Termômetro do Bitcoin" (score consolidado de compra/venda).

Inspirado em dashboards de sinais (ex.: letabuild.com/btc), combina vários
indicadores num único score de -2 (venda forte) a +2 (compra forte). Cada
indicador vira um score individual e o consolidado é a MÉDIA dos selecionados.

Dois tipos de indicador:
  1) GRÁTIS, sem chave — calculados só a partir do preço:
       - Mayer Multiple (preço / média móvel de 200 dias)
       - 200W MA Ratio  (preço / média móvel de 200 semanas)
       - RSI mensal     (força relativa no timeframe mensal)
     + Fear & Greed (já vem da alternative.me, sem chave)
  2) ON-CHAIN, com chave OPCIONAL (BGeometrics / bitcoin-data.com):
       - MVRV, SOPR, CVDD, RHODL
     Só são buscados se a variável de ambiente BGEO_API_KEY existir. Sem a
     chave, o app simplesmente usa os indicadores grátis (nunca quebra).

IMPORTANTE: nada aqui é recomendação financeira. É um exercício didático.
"""

from __future__ import annotations

import os
import json
import time

import numpy as np
import pandas as pd
import requests

HTTP_TIMEOUT = 20
USER_AGENT = "btc-mood-tracker/1.0 (educational)"

# Nome da variável de ambiente onde fica a chave da BGeometrics (opcional).
BGEO_ENV = "BGEO_API_KEY"
BGEO_BASE = "https://api.bgeometrics.com/v1"

# Cache em disco dos valores on-chain. A BGeometrics grátis limita a ~15
# requisições/dia, então guardamos o último valor de cada métrica e o
# reaproveitamos quando uma chamada falhar (rate limit) ou por até CACHE_TTL.
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_ONCHAIN_CACHE = os.path.join(_CACHE_DIR, "onchain_bgeo.json")
ONCHAIN_CACHE_TTL = 12 * 60 * 60  # 12h: valores on-chain mudam devagar


def _cache_load() -> dict:
    """Lê o cache on-chain do disco (dict métrica -> {'v':valor,'ts':epoch})."""
    try:
        with open(_ONCHAIN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(cache: dict) -> None:
    """Grava o cache on-chain no disco (silencioso se não der)."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_ONCHAIN_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


# ==========================================================================
# 1) INDICADORES GRÁTIS — calculados a partir do preço (sem chave)
# ==========================================================================

def serie_mayer_multiple(preco: pd.DataFrame) -> pd.Series:
    """
    Mayer Multiple = preço / média móvel simples de 200 dias.
    Valores baixos (<1) = barato historicamente; altos (>2.4) = esticado.
    Recebe DataFrame ['date','price'] e devolve uma Series indexada por date.
    """
    s = preco.set_index("date")["price"].astype(float)
    ma200 = s.rolling(200, min_periods=50).mean()
    return (s / ma200).rename("mayer")


def serie_200w_ratio(preco: pd.DataFrame) -> pd.Series:
    """
    200W MA Ratio = preço / média móvel de 200 SEMANAS (~1400 dias).
    Precisa de histórico longo; com pouco histórico devolve NaN (indicador
    fica indisponível, sem quebrar nada).
    """
    s = preco.set_index("date")["price"].astype(float)
    ma200w = s.rolling(1400, min_periods=400).mean()
    return (s / ma200w).rename("ma200w")


def serie_rsi_mensal(preco: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """
    RSI no timeframe MENSAL (reamostra o preço por mês e calcula o RSI).
    Reindexado de volta para diário (forward-fill) para casar com o resto.
    """
    s = preco.set_index("date")["price"].astype(float)
    mensal = s.resample("ME").last()
    delta = mensal.diff()
    ganho = delta.clip(lower=0).rolling(periodo, min_periods=periodo).mean()
    perda = (-delta.clip(upper=0)).rolling(periodo, min_periods=periodo).mean()
    rs = ganho / perda.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Volta para diário para alinhar com as outras séries.
    return rsi.reindex(s.index, method="ffill").rename("rsi_mensal")


# ==========================================================================
# 2) INDICADORES ON-CHAIN — BGeometrics / bitcoin-data.com (chave OPCIONAL)
# ==========================================================================

# Mapeia o "nome amigável" -> caminho do endpoint REAL da API bgeometrics.com.
# (CVDD/RHODL não existem nessa API; usamos os indicadores on-chain que ela
#  realmente oferece — todos clássicos de ciclo de mercado.)
BGEO_ENDPOINTS = {
    "mvrv": "mvrv",
    "sopr": "sopr",
    "mvrv_z": "mvrv-zscore",
    "nupl": "nupl",
    "puell": "puell-multiple",
    "reserve_risk": "reserve-risk",
}


def _ler_chave() -> str:
    """
    Lê a chave da BGeometrics de forma robusta, em duas fontes:
      1) variável de ambiente BGEO_API_KEY (local, HF Spaces, etc.);
      2) st.secrets["BGEO_API_KEY"] (Streamlit Cloud → Settings → Secrets),
         pois lá o secret nem sempre vira variável de ambiente.
    Devolve "" se não houver chave em lugar nenhum.
    """
    val = os.environ.get(BGEO_ENV, "").strip()
    if val:
        return val
    # st.secrets só existe quando rodando dentro do Streamlit.
    try:
        import streamlit as st
        if BGEO_ENV in st.secrets:
            return str(st.secrets[BGEO_ENV]).strip()
    except Exception:
        pass
    return ""


def tem_chave_onchain() -> bool:
    """True se houver chave da BGeometrics (env var ou st.secrets)."""
    return bool(_ler_chave())


def serie_onchain_cache(metrica: str) -> pd.DataFrame:
    """
    SÉRIE histórica de uma métrica on-chain COM CACHE em disco.

    Estratégia para conviver com o limite de ~15 requisições/dia da
    BGeometrics grátis (uma requisição traz toda a série):
      1) se a série em cache é recente (< TTL), usa o cache (0 requisição);
      2) senão tenta a API; em sucesso, atualiza o cache;
      3) se a API falhar (rate limit etc.), reaproveita o cache mesmo velho —
         melhor a série de ontem do que "indisponível".

    Retorna DataFrame ['date','valor'] (vazio se nunca houve dado).
    """
    cache = _cache_load()
    entrada = cache.get(metrica)
    agora = time.time()

    def _do_cache_para_df(e) -> pd.DataFrame:
        try:
            df = pd.DataFrame(e["serie"])
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "valor"]]
        except Exception:
            return pd.DataFrame(columns=["date", "valor"])

    # 1) Cache recente -> usa direto.
    if entrada and (agora - entrada.get("ts", 0)) < ONCHAIN_CACHE_TTL:
        df = _do_cache_para_df(entrada)
        if not df.empty:
            return df

    # 2) Tenta a API (série completa).
    serie = fetch_onchain_serie(metrica)
    if not serie.empty:
        cache[metrica] = {
            "ts": agora,
            "serie": [{"date": d.strftime("%Y-%m-%d"), "valor": float(v)}
                      for d, v in zip(serie["date"], serie["valor"])],
        }
        _cache_save(cache)
        return serie

    # 3) Falhou -> usa cache antigo, se houver.
    if entrada:
        return _do_cache_para_df(entrada)
    return pd.DataFrame(columns=["date", "valor"])


def fetch_onchain_bgeometrics(metrica: str) -> float:
    """Último valor de uma métrica on-chain (usa a série cacheada)."""
    serie = serie_onchain_cache(metrica)
    if serie.empty:
        return float("nan")
    return float(serie["valor"].dropna().iloc[-1])


def fetch_onchain_serie(metrica: str) -> pd.DataFrame:
    """
    Baixa a SÉRIE HISTÓRICA completa de uma métrica on-chain na BGeometrics.

    Uma única requisição traz todo o histórico (atende ao gráfico histórico E
    ao valor atual). Retorna DataFrame ['date','valor'] ou vazio.

    Parsing defensivo: aceita lista de dicts com um campo de data (d/date/
    unixTs/...) e um campo numérico (o valor da métrica). Qualquer falha vira
    DataFrame vazio (indicador "indisponível", sem quebrar o app).
    """
    chave = _ler_chave()
    if not chave:
        return pd.DataFrame(columns=["date", "valor"])

    endpoint = BGEO_ENDPOINTS.get(metrica, metrica)
    # A bgeometrics.com autentica via query param ?token=... (não header).
    url = f"{BGEO_BASE}/{endpoint}"
    try:
        r = requests.get(url, params={"token": chave}, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        dados = r.json()
    except Exception as e:
        print(f"[BGeo {metrica}] indisponível: {e}")
        return pd.DataFrame(columns=["date", "valor"])

    registros = dados if isinstance(dados, list) else dados.get("data", [])
    if not isinstance(registros, list) or not registros:
        return pd.DataFrame(columns=["date", "valor"])

    linhas = []
    for reg in registros:
        if not isinstance(reg, dict):
            continue
        data_val, num_val = None, None
        for k, v in reg.items():
            kl = k.lower()
            if kl in ("d", "date"):
                data_val = v
            elif kl in ("unixts", "unix_ts", "timestamp", "t", "time"):
                # timestamp epoch (fallback se não houver 'd'/'date')
                if data_val is None:
                    try:
                        data_val = pd.to_datetime(int(v), unit="s")
                    except (TypeError, ValueError):
                        pass
            elif num_val is None:
                try:
                    num_val = float(v)
                except (TypeError, ValueError):
                    pass
        if data_val is None or num_val is None:
            continue
        try:
            d = pd.to_datetime(data_val).normalize()
            if d.tzinfo is not None:  # tz-naive p/ casar com o índice do preço
                d = d.tz_localize(None)
        except Exception:
            continue
        linhas.append({"date": d, "valor": num_val})

    if not linhas:
        return pd.DataFrame(columns=["date", "valor"])
    return (pd.DataFrame(linhas).dropna()
            .drop_duplicates("date").sort_values("date").reset_index(drop=True))


# ==========================================================================
# 3) SCORING — converte VALOR de cada indicador em score -2..+2
# ==========================================================================

def _score_por_faixas(valor: float, faixas: list[tuple[float, int]]) -> int:
    """
    Converte um valor num score usando faixas (limiar_superior, score),
    em ordem crescente de limiar. Retorna o score da primeira faixa cujo
    limiar >= valor; se passar de todas, usa o score da última.

    Ex.: faixas=[(1.0, 2), (1.5, 1), (2.4, 0), (3.0, -1), (inf, -2)]
    """
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return 0  # indicador indisponível -> neutro (e será ignorado a parte)
    for limiar, score in faixas:
        if valor <= limiar:
            return score
    return faixas[-1][1]


# Faixas de score por indicador (calibradas de forma didática/conservadora).
# Quanto menor o valor "barato", maior o score de COMPRA (+2).
FAIXAS = {
    # --- Grátis (do preço) ---
    "mayer":      [(0.8, 2), (1.0, 1), (1.5, 0), (2.4, -1), (float("inf"), -2)],
    "ma200w":     [(1.0, 2), (1.5, 1), (3.0, 0), (5.0, -1), (float("inf"), -2)],
    "rsi_mensal": [(30, 2), (45, 1), (60, 0), (70, -1), (float("inf"), -2)],
    "fng":        [(20, 2), (40, 1), (60, 0), (80, -1), (float("inf"), -2)],
    # --- On-chain (bgeometrics) ---
    "mvrv":         [(1.0, 2), (1.5, 1), (2.5, 0), (3.5, -1), (float("inf"), -2)],
    "sopr":         [(0.95, 2), (1.0, 1), (1.02, 0), (1.05, -1), (float("inf"), -2)],
    "mvrv_z":       [(0.0, 2), (2.0, 1), (4.0, 0), (6.0, -1), (float("inf"), -2)],
    "nupl":         [(0.0, 2), (0.25, 1), (0.5, 0), (0.75, -1), (float("inf"), -2)],
    "puell":        [(0.5, 2), (1.0, 1), (2.0, 0), (4.0, -1), (float("inf"), -2)],
    "reserve_risk": [(0.002, 2), (0.005, 1), (0.01, 0), (0.02, -1), (float("inf"), -2)],
}

# Rótulos legíveis e nomes de exibição.
NOMES = {
    "mayer": "Mayer Multiple", "ma200w": "200W MA Ratio",
    "rsi_mensal": "RSI Mensal", "fng": "Fear & Greed",
    "mvrv": "MVRV Ratio", "sopr": "SOPR", "mvrv_z": "MVRV Z-Score",
    "nupl": "NUPL", "puell": "Puell Multiple", "reserve_risk": "Reserve Risk",
}

# Quais indicadores são on-chain (precisam de chave). Espelha BGEO_ENDPOINTS.
ONCHAIN = {"mvrv", "sopr", "mvrv_z", "nupl", "puell", "reserve_risk"}

# Explicação didática de cada indicador (para tooltips/expander no dashboard).
EXPLICACOES = {
    "mayer": "Preço ÷ média de 200 dias. <1 = barato; >2.4 historicamente "
             "marca topos (esticado).",
    "ma200w": "Preço ÷ média de 200 semanas. Perto de 1 costuma marcar fundos "
              "de ciclo; muito acima indica euforia.",
    "rsi_mensal": "Força relativa no timeframe mensal (0–100). <30 = sobrevendido "
                  "(compra); >70 = sobrecomprado (venda).",
    "fng": "Índice de Medo & Ganância (0–100). Medo extremo (baixo) tende a ser "
           "oportunidade; ganância extrema (alto), cautela.",
    "mvrv": "Valor de mercado ÷ valor realizado. <1 = mercado abaixo do custo "
            "médio (barato); >3.5 = topo histórico.",
    "sopr": "Spent Output Profit Ratio. <1 = moedas movidas no prejuízo "
            "(capitulação/compra); >1 = realização de lucro.",
    "mvrv_z": "MVRV padronizado (z-score). Valores baixos marcam fundos; "
              ">6–7 marcam topos de ciclo.",
    "nupl": "Net Unrealized Profit/Loss. <0 = mercado no prejuízo (medo/compra); "
            ">0.75 = euforia (venda).",
    "puell": "Puell Multiple (receita de mineradores vs média). Baixo = pressão "
             "em mineradores (fundo); alto = topo.",
    "reserve_risk": "Confiança vs preço. Valores baixos = ótima relação "
                    "risco/retorno (acumulação); altos = caro.",
}


def score_para_sinal(score: float) -> str:
    """Converte um score numérico (-2..+2) no rótulo do sinal."""
    if score >= 1.5:
        return "COMPRA FORTE"
    if score >= 0.5:
        return "COMPRA"
    if score > -0.5:
        return "NEUTRO"
    if score > -1.5:
        return "VENDA"
    return "VENDA FORTE"


def score_indicador(chave: str, valor: float) -> int:
    """Score -2..+2 de um indicador, pelas suas faixas."""
    return _score_por_faixas(valor, FAIXAS[chave])


# ==========================================================================
# 4) MONTAGEM DO TERMÔMETRO (snapshot atual) e SÉRIE HISTÓRICA do score
# ==========================================================================

def montar_snapshot(preco: pd.DataFrame, fng_atual: float | None,
                    incluir_onchain: bool = True) -> pd.DataFrame:
    """
    Monta a tabela do termômetro com o valor ATUAL de cada indicador, seu
    sinal e score. Indicadores indisponíveis (NaN) são marcados e não entram
    no consolidado.

    Retorna DataFrame com colunas:
        ['chave', 'indicador', 'valor', 'sinal', 'score', 'onchain', 'ok']
    """
    valores: dict[str, float] = {}

    # --- Grátis (do preço) ---
    valores["mayer"] = serie_mayer_multiple(preco).dropna().iloc[-1] \
        if not serie_mayer_multiple(preco).dropna().empty else float("nan")
    s200 = serie_200w_ratio(preco).dropna()
    valores["ma200w"] = s200.iloc[-1] if not s200.empty else float("nan")
    rsi = serie_rsi_mensal(preco).dropna()
    valores["rsi_mensal"] = rsi.iloc[-1] if not rsi.empty else float("nan")
    valores["fng"] = float(fng_atual) if fng_atual is not None else float("nan")

    # --- On-chain (chave opcional) ---
    if incluir_onchain and tem_chave_onchain():
        for m in BGEO_ENDPOINTS:  # mvrv, sopr, mvrv_z, nupl, puell, reserve_risk
            valores[m] = fetch_onchain_bgeometrics(m)

    linhas = []
    for chave, valor in valores.items():
        ok = not (valor is None or (isinstance(valor, float) and np.isnan(valor)))
        sc = score_indicador(chave, valor) if ok else 0
        linhas.append({
            "chave": chave,
            "indicador": NOMES.get(chave, chave),
            "valor": valor,
            "sinal": score_para_sinal(sc) if ok else "—",
            "score": sc if ok else None,
            "onchain": chave in ONCHAIN,
            "ok": ok,
        })
    return pd.DataFrame(linhas)


def consolidar(snapshot: pd.DataFrame, selecionados: list[str] | None = None,
               pesos: dict[str, float] | None = None) -> float:
    """
    Score consolidado = média (PONDERADA, se `pesos` for dado) dos scores dos
    indicadores SELECIONADOS e disponíveis. Sem `selecionados`, usa todos os
    disponíveis; sem `pesos`, peso 1 para cada (média simples).
    """
    df = snapshot[snapshot["ok"]]
    if selecionados is not None:
        df = df[df["chave"].isin(selecionados)]
    if df.empty:
        return float("nan")
    if not pesos:
        return float(df["score"].mean())
    w = df["chave"].map(lambda c: float(pesos.get(c, 1.0)))
    soma_w = w.sum()
    if soma_w == 0:
        return float("nan")
    return float((df["score"] * w).sum() / soma_w)


def serie_score_historico(preco: pd.DataFrame, fng: pd.DataFrame,
                          selecionados: list[str] | None = None,
                          incluir_onchain: bool = False,
                          pesos: dict[str, float] | None = None) -> pd.DataFrame:
    """
    Recalcula o score consolidado AO LONGO DO TEMPO.

    Sempre usa os indicadores GRÁTIS com série (Mayer, 200W MA, RSI mensal) +
    Fear & Greed. Se `incluir_onchain=True` e houver chave, acrescenta as
    séries históricas dos on-chain (MVRV, SOPR, ...), cada uma via cache para
    poupar requisições. `pesos` permite média ponderada (peso por indicador).

    Retorna DataFrame ['date','price','score'].
    """
    base = preco.set_index("date")[["price"]].copy()
    series = {
        "mayer": serie_mayer_multiple(preco),
        "ma200w": serie_200w_ratio(preco),
        "rsi_mensal": serie_rsi_mensal(preco),
    }
    if fng is not None and not fng.empty:
        series["fng"] = fng.set_index("date")["fng"].astype(float)

    # On-chain (opcional): cada métrica vira uma série alinhada por data.
    if incluir_onchain and tem_chave_onchain():
        for m in BGEO_ENDPOINTS:
            s = serie_onchain_cache(m)
            if not s.empty:
                series[m] = s.set_index("date")["valor"].astype(float)

    # Mantém só os indicadores selecionados (ou todos os disponíveis).
    chaves = [k for k in series
              if selecionados is None or k in selecionados]

    df = base.copy()
    for k in chaves:
        df[k] = series[k].reindex(df.index, method="ffill")

    def _linha_score(row):
        num, den = 0.0, 0.0
        for k in chaves:
            v = row.get(k)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                w = float(pesos.get(k, 1.0)) if pesos else 1.0
                num += score_indicador(k, v) * w
                den += w
        return num / den if den else np.nan

    df["score"] = df.apply(_linha_score, axis=1)
    out = df.reset_index()[["date", "price", "score"]].dropna(subset=["score"])
    return out.reset_index(drop=True)
