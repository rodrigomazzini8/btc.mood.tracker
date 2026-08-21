# -*- coding: utf-8 -*-
"""
coinmetrics.py — on-chain do Bitcoin de graça, sem chave e sem cadastro.

A Coin Metrics publica o dataset "community" do BTC como um CSV público no
GitHub: série diária desde 2010 com **market cap**, **MVRV** (de onde sai o
realized cap) e **emissão em USD**. Com isso dá para reconstruir as métricas
de ciclo mais importantes sem nenhuma credencial:

    MVRV          = CapMVRVCur
    realized cap  = market cap / MVRV
    MVRV Z-Score  = (market cap − realized cap) / desvio-padrão(market cap)
    NUPL          = 1 − 1/MVRV
    Puell Multiple= emissão do dia / média de 365 dias da emissão

Usado pelo `cycle_model.py` e pelo `termometro.py` — os dois compartilham
este cache, então o CSV (~2,5 MB) é baixado no máximo 1× a cada 12h.

O CSV também traz o **preço**, o que serve de fallback quando Binance e
CoinGecko estão bloqueadas no servidor (acontece em datacenter/nuvem).

Uso não comercial. Crédito: Coin Metrics Community Data
(https://github.com/coinmetrics/data).
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd


CM_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
CM_COLUNAS = ["time", "PriceUSD", "CapMrktCurUSD", "CapMVRVCur", "IssTotUSD"]
CM_CACHE_TTL = 12 * 60 * 60  # o CSV é atualizado 1x/dia

_CACHE_DIR_CM = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
CM_CACHE = os.path.join(_CACHE_DIR_CM, "coinmetrics_btc.csv")


def _cm_do_csv(caminho) -> pd.DataFrame:
    """Lê o CSV da Coin Metrics e deriva as métricas de ciclo."""
    df = pd.read_csv(caminho, usecols=CM_COLUNAS, parse_dates=["time"])
    df = (df.rename(columns={"time": "date", "PriceUSD": "price"})
          .dropna(subset=["price"]).sort_values("date").reset_index(drop=True))

    mc = df.set_index("date")["CapMrktCurUSD"].astype(float)
    mvrv = df.set_index("date")["CapMVRVCur"].astype(float)
    rc = mc / mvrv.replace(0, np.nan)        # realized cap = market cap / MVRV

    out = pd.DataFrame({"date": df["date"], "price": df["price"]})
    out["cm_mvrv"] = mvrv.to_numpy()
    # MVRV Z-Score = (market cap − realized cap) / desvio-padrão do market cap.
    # `expanding` (só o passado) para o histórico não ter look-ahead.
    out["cm_mvrv_z"] = ((mc - rc) / mc.expanding(min_periods=365).std()).to_numpy()
    # NUPL = lucro não realizado / market cap = 1 − realized/market.
    out["cm_nupl"] = (1.0 - 1.0 / mvrv.replace(0, np.nan)).to_numpy()
    # Puell Multiple = emissão diária em USD / média de 365 dias dela.
    iss = df.set_index("date")["IssTotUSD"].astype(float)
    out["cm_puell"] = (iss / iss.rolling(365, min_periods=200).mean()).to_numpy()
    return out


def fetch_coinmetrics(usar_cache: bool = True) -> pd.DataFrame:
    """
    Baixa (ou lê do cache) o dataset da Coin Metrics e devolve
    ['date','price','cm_mvrv','cm_mvrv_z','cm_nupl','cm_puell'].

    O arquivo tem ~2,5 MB e fica cacheado em `cache/coinmetrics_btc.csv` por
    12h. Qualquer falha devolve DataFrame vazio — o modelo cai nos proxies de
    preço e o card mostra o selo "proxy". Nunca quebra o app.
    """
    if usar_cache and os.path.exists(CM_CACHE):
        idade = time.time() - os.path.getmtime(CM_CACHE)
        if idade < CM_CACHE_TTL:
            try:
                return _cm_do_csv(CM_CACHE)
            except Exception:
                pass  # cache corrompido: baixa de novo

    try:
        import requests
        r = requests.get(CM_URL, timeout=60, headers={
            "User-Agent": "btc-mood-tracker/1.0 (educational)"})
        r.raise_for_status()
        os.makedirs(_CACHE_DIR_CM, exist_ok=True)
        with open(CM_CACHE, "wb") as f:
            f.write(r.content)
        return _cm_do_csv(CM_CACHE)
    except Exception as e:
        print(f"[Coin Metrics] indisponível: {e}")
        if os.path.exists(CM_CACHE):   # sem rede: cache velho é melhor que nada
            try:
                return _cm_do_csv(CM_CACHE)
            except Exception:
                pass
        return pd.DataFrame(columns=["date", "price", "cm_mvrv", "cm_mvrv_z",
                                     "cm_nupl", "cm_puell"])
