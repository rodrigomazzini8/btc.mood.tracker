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
import time

import numpy as np
import pandas as pd
import requests

HTTP_TIMEOUT = 20
USER_AGENT = "btc-mood-tracker/1.0 (educational)"

# Nome da variável de ambiente onde fica a chave da BGeometrics (opcional).
BGEO_ENV = "BGEO_API_KEY"
BGEO_BASE = "https://bitcoin-data.com/v1"


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

# Mapeia o "nome amigável" -> caminho do endpoint na API da BGeometrics.
# (Os nomes podem variar conforme a doc; ajuste aqui se necessário.)
BGEO_ENDPOINTS = {
    "mvrv": "mvrv",
    "sopr": "sopr",
    "cvdd": "cvdd",
    "rhodl": "rhodl-ratio",
}


def tem_chave_onchain() -> bool:
    """True se a variável de ambiente com a chave da BGeometrics existir."""
    return bool(os.environ.get(BGEO_ENV, "").strip())


def fetch_onchain_bgeometrics(metrica: str) -> float:
    """
    Busca o ÚLTIMO valor de uma métrica on-chain na BGeometrics.

    Requer a chave em os.environ[BGEO_ENV]. Sem chave, devolve NaN.
    É defensivo: qualquer falha (rede, formato, rate limit) vira NaN, então
    o indicador apenas fica "indisponível" sem derrubar o app.

    A API devolve um histórico tipo [{"d": "2024-01-01", "<metrica>": "1.23"},
    ...]; pegamos o último ponto com valor numérico.
    """
    chave = os.environ.get(BGEO_ENV, "").strip()
    if not chave:
        return float("nan")

    endpoint = BGEO_ENDPOINTS.get(metrica, metrica)
    url = f"{BGEO_BASE}/{endpoint}"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT, "x-api-key": chave})
        r.raise_for_status()
        dados = r.json()
    except Exception as e:
        print(f"[BGeo {metrica}] indisponível: {e}")
        return float("nan")

    # A resposta pode ser lista de dicts ou um dict. Extraímos o último número.
    registros = dados if isinstance(dados, list) else dados.get("data", [])
    if not isinstance(registros, list) or not registros:
        return float("nan")

    for reg in reversed(registros):
        if not isinstance(reg, dict):
            continue
        # Procura qualquer campo numérico que não seja data/timestamp.
        for k, v in reg.items():
            if k.lower() in ("d", "date", "unixts", "timestamp", "t"):
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return float("nan")


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
    "mayer":      [(0.8, 2), (1.0, 1), (1.5, 0), (2.4, -1), (float("inf"), -2)],
    "ma200w":     [(1.0, 2), (1.5, 1), (3.0, 0), (5.0, -1), (float("inf"), -2)],
    "rsi_mensal": [(30, 2), (45, 1), (60, 0), (70, -1), (float("inf"), -2)],
    "fng":        [(20, 2), (40, 1), (60, 0), (80, -1), (float("inf"), -2)],
    "mvrv":       [(1.0, 2), (1.5, 1), (2.5, 0), (3.5, -1), (float("inf"), -2)],
    "sopr":       [(0.95, 2), (1.0, 1), (1.02, 0), (1.05, -1), (float("inf"), -2)],
    "cvdd":       [(1.0, 2), (1.5, 1), (2.5, 0), (4.0, -1), (float("inf"), -2)],
    "rhodl":      [(0.3, 2), (0.6, 1), (1.0, 0), (2.5, -1), (float("inf"), -2)],
}

# Rótulos legíveis e nomes de exibição.
NOMES = {
    "mayer": "Mayer Multiple", "ma200w": "200W MA Ratio",
    "rsi_mensal": "RSI Mensal", "fng": "Fear & Greed",
    "mvrv": "MVRV Ratio", "sopr": "SOPR", "cvdd": "CVDD", "rhodl": "RHODL Ratio",
}

# Quais indicadores são on-chain (precisam de chave).
ONCHAIN = {"mvrv", "sopr", "cvdd", "rhodl"}


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
        for m in ("mvrv", "sopr", "cvdd", "rhodl"):
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


def consolidar(snapshot: pd.DataFrame, selecionados: list[str] | None = None) -> float:
    """
    Score consolidado = média dos scores dos indicadores SELECIONADOS e
    disponíveis. Se `selecionados` for None, usa todos os disponíveis.
    """
    df = snapshot[snapshot["ok"]]
    if selecionados is not None:
        df = df[df["chave"].isin(selecionados)]
    if df.empty:
        return float("nan")
    return float(df["score"].mean())


def serie_score_historico(preco: pd.DataFrame, fng: pd.DataFrame,
                          selecionados: list[str] | None = None) -> pd.DataFrame:
    """
    Recalcula o score consolidado AO LONGO DO TEMPO usando os indicadores
    GRÁTIS que têm série histórica (Mayer, 200W MA, RSI mensal) + Fear & Greed.
    (On-chain entra só no snapshot atual, pois o histórico depende da API.)

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

    # Mantém só os indicadores grátis selecionados (ou todos os grátis).
    chaves = [k for k in series
              if selecionados is None or k in selecionados]

    df = base.copy()
    for k in chaves:
        df[k] = series[k].reindex(df.index, method="ffill")

    def _linha_score(row):
        scores = []
        for k in chaves:
            v = row.get(k)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                scores.append(score_indicador(k, v))
        return np.mean(scores) if scores else np.nan

    df["score"] = df.apply(_linha_score, axis=1)
    out = df.reset_index()[["date", "price", "score"]].dropna(subset=["score"])
    return out.reset_index(drop=True)
