# -*- coding: utf-8 -*-
"""
coinmetrics.py — on-chain do Bitcoin de graça, sem chave, com no máximo
**1 dia de atraso**.

De onde vem o dado (nesta ordem, a primeira que responder e estiver em dia):

  1. **API community da Coin Metrics** (`community-api.coinmetrics.io`).
     Grátis, sem cadastro, atualizada diariamente. É a fonte primária.
  2. **Snapshot no próprio repositório** (`data/onchain_btc.csv`), atualizado
     todo dia por um GitHub Action (`.github/workflows/dados.yml`). Garante
     dado fresco mesmo onde a API está bloqueada — datacenter, firewall
     corporativo, Streamlit Cloud com rede restrita.
  3. **CSV histórico no GitHub da Coin Metrics** — último recurso. ATENÇÃO:
     esse mirror **parou de ser atualizado em 24/05/2026**; serve só para
     histórico longo quando não há mais nada.

Com market cap, MVRV e emissão dá para reconstruir as métricas de ciclo:

    MVRV          = CapMVRVCur
    realized cap  = market cap / MVRV
    MVRV Z-Score  = (market cap − realized cap) / desvio-padrão(market cap)
    NUPL          = 1 − 1/MVRV
    Puell Multiple= emissão do dia / média de 365 dias da emissão

O DataFrame devolvido carrega em `.attrs` de onde veio (`origem`), até quando
vai (`ate`) e o atraso em dias (`atraso`) — o `cycle_model` usa isso para
avisar quando o dado envelhece.

Uso não comercial. Crédito: Coin Metrics Community Data.
"""

from __future__ import annotations

import os
import time
import datetime as dt

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Fontes
# --------------------------------------------------------------------------
CM_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_METRICAS = ["PriceUSD", "CapMrktCurUSD", "CapMVRVCur", "IssTotUSD"]

# Mirror histórico (congelado em 24/05/2026 — só como último recurso).
CM_MIRROR = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"

COLUNAS_BRUTAS = ["date"] + CM_METRICAS
USER_AGENT = "btc-mood-tracker/1.0 (educational)"

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(_RAIZ, "data", "onchain_btc.csv")
CACHE = os.path.join(_RAIZ, "cache", "coinmetrics_bruto.csv")

CACHE_TTL = 6 * 60 * 60        # revalida a cada 6h...
MAX_ATRASO_CACHE = 2           # ...e ignora o cache se ele já estiver velho


# --------------------------------------------------------------------------
# Leitura e derivação
# --------------------------------------------------------------------------

def _normalizar(bruto: pd.DataFrame) -> pd.DataFrame:
    """Deixa qualquer origem no mesmo formato: ['date'] + CM_METRICAS."""
    df = bruto.rename(columns={"time": "date"})
    faltando = [c for c in COLUNAS_BRUTAS if c not in df.columns]
    if faltando:
        raise ValueError(f"colunas ausentes: {faltando}")
    df = df[COLUNAS_BRUTAS].copy()
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df["date"] = df["date"].dt.tz_localize(None).dt.normalize()
    for c in CM_METRICAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (df.dropna(subset=["date", "PriceUSD"])
            .drop_duplicates("date", keep="last")
            .sort_values("date").reset_index(drop=True))


def _derivar(bruto: pd.DataFrame) -> pd.DataFrame:
    """Métricas de ciclo a partir das colunas cruas."""
    df = _normalizar(bruto)
    mc = df.set_index("date")["CapMrktCurUSD"]
    mvrv = df.set_index("date")["CapMVRVCur"]
    rc = mc / mvrv.replace(0, np.nan)          # realized cap = market cap / MVRV

    out = pd.DataFrame({"date": df["date"], "price": df["PriceUSD"]})
    out["cm_mvrv"] = mvrv.to_numpy()
    # MVRV Z-Score = (market cap − realized cap) / desvio-padrão do market cap.
    # `expanding` (só o passado) para o histórico não ter look-ahead.
    out["cm_mvrv_z"] = ((mc - rc) / mc.expanding(min_periods=365).std()).to_numpy()
    # NUPL = lucro não realizado / market cap = 1 − realized/market.
    out["cm_nupl"] = (1.0 - 1.0 / mvrv.replace(0, np.nan)).to_numpy()
    # Puell Multiple = emissão diária em USD / média de 365 dias dela.
    iss = df.set_index("date")["IssTotUSD"]
    out["cm_puell"] = (iss / iss.rolling(365, min_periods=200).mean()).to_numpy()

    out.attrs["ate"] = out["date"].max()
    out.attrs["atraso"] = atraso_em_dias(out)
    return out


def _cm_do_csv(caminho) -> pd.DataFrame:
    """Lê um CSV (mirror ou snapshot) e devolve as métricas derivadas."""
    return _derivar(pd.read_csv(caminho))


def atraso_em_dias(df: pd.DataFrame, hoje: pd.Timestamp | None = None) -> int:
    """Dias entre a última data do dado e hoje (999 se não houver dado)."""
    if df is None or len(df) == 0 or "date" not in df.columns:
        return 999
    hoje = pd.Timestamp(hoje or dt.date.today()).normalize()
    return int((hoje - pd.Timestamp(df["date"].max()).normalize()).days)


# --------------------------------------------------------------------------
# 1) API community — a fonte que está sempre em dia
# --------------------------------------------------------------------------

def fetch_api(timeout: int = 45, max_paginas: int = 8) -> pd.DataFrame:
    """
    Série diária completa pela API community da Coin Metrics (sem chave).

    A API pagina; seguimos `next_page_url` até acabar (com teto de páginas,
    para nunca virar laço infinito). Devolve as colunas CRUAS; qualquer falha
    devolve DataFrame vazio.
    """
    try:
        import requests
    except Exception:
        return pd.DataFrame(columns=COLUNAS_BRUTAS)

    params = {
        "assets": "btc",
        "metrics": ",".join(CM_METRICAS),
        "frequency": "1d",
        "page_size": 10000,
        "paging_from": "start",
    }
    url, linhas = CM_API, []
    try:
        for _ in range(max_paginas):
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            corpo = r.json()
            linhas.extend(corpo.get("data", []))
            proxima = corpo.get("next_page_url")
            if not proxima:
                break
            url, params = proxima, None  # a próxima URL já vem com os parâmetros
    except Exception as e:
        print(f"[Coin Metrics API] indisponível: {e}")
        return pd.DataFrame(columns=COLUNAS_BRUTAS)

    if not linhas:
        return pd.DataFrame(columns=COLUNAS_BRUTAS)
    df = pd.DataFrame(linhas)
    try:
        return _normalizar(df)
    except Exception as e:
        print(f"[Coin Metrics API] resposta inesperada: {e}")
        return pd.DataFrame(columns=COLUNAS_BRUTAS)


# --------------------------------------------------------------------------
# 2) Snapshot no repositório — atualizado 1x/dia pelo GitHub Action
# --------------------------------------------------------------------------

def ler_snapshot(caminho: str | None = None) -> pd.DataFrame:
    """
    Lê o snapshot versionado no repositório (colunas cruas).

    O caminho é resolvido na CHAMADA, não na definição da função: assim dá
    para apontar `SNAPSHOT` para outro lugar (teste, outro repositório) e a
    mudança valer de verdade.
    """
    try:
        return _normalizar(pd.read_csv(caminho or SNAPSHOT))
    except Exception:
        return pd.DataFrame(columns=COLUNAS_BRUTAS)


def salvar_snapshot(bruto: pd.DataFrame, caminho: str | None = None) -> str:
    """
    Grava o snapshot com valores ARREDONDADOS.

    O arredondamento não é estética: sem ele, o ruído do último dígito muda a
    série inteira a cada dia e o git guarda um arquivo novo por commit. Com
    ele, o diário vira "acrescenta uma linha" e o repositório não incha.
    """
    caminho = caminho or SNAPSHOT
    df = _normalizar(bruto).copy()
    df["PriceUSD"] = df["PriceUSD"].round(2)
    df["CapMrktCurUSD"] = df["CapMrktCurUSD"].round(0)
    df["CapMVRVCur"] = df["CapMVRVCur"].round(6)
    df["IssTotUSD"] = df["IssTotUSD"].round(2)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    df.to_csv(caminho, index=False, date_format="%Y-%m-%d")
    return caminho


# --------------------------------------------------------------------------
# 3) Mirror histórico no GitHub (congelado em maio/2026)
# --------------------------------------------------------------------------

def fetch_mirror(timeout: int = 60) -> pd.DataFrame:
    """CSV histórico da Coin Metrics no GitHub. Só como último recurso."""
    try:
        import requests
        r = requests.get(CM_MIRROR, timeout=timeout,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        import io
        bruto = pd.read_csv(io.StringIO(r.text), usecols=lambda c: c in COLUNAS_BRUTAS
                            or c == "time")
        return _normalizar(bruto)
    except Exception as e:
        print(f"[Coin Metrics mirror] indisponível: {e}")
        return pd.DataFrame(columns=COLUNAS_BRUTAS)


# --------------------------------------------------------------------------
# Orquestração: pega o dado mais fresco que conseguir
# --------------------------------------------------------------------------

def _cache_utilizavel() -> pd.DataFrame:
    """Cache local, se for recente E não estiver velho."""
    if not os.path.exists(CACHE):
        return pd.DataFrame(columns=COLUNAS_BRUTAS)
    if time.time() - os.path.getmtime(CACHE) > CACHE_TTL:
        return pd.DataFrame(columns=COLUNAS_BRUTAS)
    bruto = ler_snapshot(CACHE)
    # Cache recente mas com dado velho não serve: é melhor tentar a rede de
    # novo do que ficar 6h servindo uma leitura defasada.
    if atraso_em_dias(bruto) > MAX_ATRASO_CACHE:
        return pd.DataFrame(columns=COLUNAS_BRUTAS)
    return bruto


def fetch_bruto(usar_cache: bool = True) -> pd.DataFrame:
    """
    Colunas cruas, da fonte mais fresca disponível. `.attrs['origem']` diz
    qual foi. Nunca levanta exceção: no pior caso devolve vazio.
    """
    if usar_cache:
        bruto = _cache_utilizavel()
        if not bruto.empty:
            bruto.attrs["origem"] = "cache"
            return bruto

    tentativas = [("api", fetch_api), ("snapshot", ler_snapshot),
                  ("mirror", fetch_mirror)]
    melhor = pd.DataFrame(columns=COLUNAS_BRUTAS)
    melhor.attrs["origem"] = "—"
    for origem, buscar in tentativas:
        try:
            bruto = buscar()
        except Exception:
            continue
        if bruto is None or bruto.empty:
            continue
        bruto.attrs["origem"] = origem
        if atraso_em_dias(bruto) <= MAX_ATRASO_CACHE:
            _guardar_cache(bruto)
            return bruto                       # em dia: para por aqui
        if melhor.empty or bruto["date"].max() > melhor["date"].max():
            melhor = bruto                     # guarda o menos velho e segue

    if not melhor.empty:
        _guardar_cache(melhor)
    return melhor


def _guardar_cache(bruto: pd.DataFrame) -> None:
    try:
        salvar_snapshot(bruto, CACHE)
    except Exception:
        pass


def fetch_coinmetrics(usar_cache: bool = True) -> pd.DataFrame:
    """
    Métricas de ciclo prontas: ['date','price','cm_mvrv','cm_mvrv_z',
    'cm_nupl','cm_puell'], da fonte mais fresca que responder.

    `.attrs` traz `origem` (api/snapshot/mirror/cache), `ate` (última data) e
    `atraso` (dias). Falhou tudo? DataFrame vazio — e o modelo cai nos proxies
    de preço, avisando.
    """
    bruto = fetch_bruto(usar_cache=usar_cache)
    if bruto is None or bruto.empty:
        vazio = pd.DataFrame(columns=["date", "price", "cm_mvrv", "cm_mvrv_z",
                                      "cm_nupl", "cm_puell"])
        vazio.attrs.update({"origem": "—", "ate": None, "atraso": 999})
        return vazio
    origem = bruto.attrs.get("origem", "—")
    dados = _derivar(bruto)
    dados.attrs["origem"] = origem
    return dados
