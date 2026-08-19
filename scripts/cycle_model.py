# -*- coding: utf-8 -*-
"""
cycle_model.py — "BTC Cycle Model": score de CICLO de 0 a 100.

Diferença para o `termometro.py` (que já existe no projeto):

  - O termômetro dá um score -2..+2 de "compra/venda" a partir de uma média
    de indicadores soltos, cada um com faixas discretas.
  - Aqui o objetivo é outro: dizer **em que ponto do ciclo de mercado o
    Bitcoin está** (0 = fundo profundo, 100 = euforia), com uma escala
    CONTÍNUA (interpolação, não degraus), organizada em **pilares** que
    replicam a leitura on-chain clássica de ciclo, e traduzir esse número
    em **gestão de posição** (quanto acumular, quanto realizar).

Pilares do modelo (cada um com fonte on-chain "de verdade" e um FALLBACK
grátis calculado só do preço, para o modelo nunca ficar mudo):

  | Pilar                | On-chain (chave)      | Fallback grátis (preço)        |
  |----------------------|-----------------------|--------------------------------|
  | Valuation            | MVRV Z-Score          | Z da razão preço/MA200W        |
  | Lucro não realizado  | NUPL                  | NUPL proxy = 1 − MA200W/preço  |
  | Oferta em lucro      | Supply in Profit      | % de dias (4a) abaixo do preço |
  | Mãos longas / ciclo  | RHODL Ratio           | Drawdown do topo histórico     |
  | Realização de lucro  | SOPR                  | RSI mensal                     |
  | Ciclo & sentimento   | —                     | Mayer + Fear&Greed + halving   |

O fallback do Valuation/NUPL se apoia num fato conhecido do mercado: a
**média móvel de 200 semanas** anda historicamente colada no **realized
price** (custo médio da rede). Então `preço / MA200W` é um proxy razoável de
MVRV, e `1 − MA200W/preço` é um proxy de NUPL. É proxy, não é a métrica
real — e a interface deixa isso explícito em cada linha.

Escala do score (0..100) e fases:

     0 ─────── 15 ─────── 35 ─────── 60 ─────── 80 ─────── 100
      FUNDO      ACUMU-     EXPANSÃO   DISTRI-     EUFORIA
     PROFUNDO    LAÇÃO                 BUIÇÃO

Uso rápido:
    import cycle_model as cm
    res = cm.calcular(preco_df, fng_atual=42)
    print(res["score"], res["fase"], res["plano"]["acao"])

Autoteste offline (sem rede, com série sintética):
    python scripts/cycle_model.py --autoteste

IMPORTANTE: nada aqui é recomendação financeira. É um exercício didático de
modelagem de ciclo. Modelos de ciclo erram, e os limiares de "fundo" e
"topo" mudam a cada ciclo (o mercado amadurece, a volatilidade cai).
"""

from __future__ import annotations


import numpy as np
import pandas as pd

try:  # o modelo reaproveita o cache/rede on-chain que o termômetro já tem
    import termometro as term
except Exception:  # pragma: no cover - import opcional (autoteste sem rede)
    term = None


# ==========================================================================
# 1) ESCALAS — converte o VALOR de cada métrica num sub-score 0..100
# ==========================================================================
# Cada escala é uma lista de pontos (valor, score) em ordem crescente de
# valor. Entre os pontos interpolamos linearmente (np.interp), e fora das
# pontas o valor fica "grudado" no extremo (0 ou 100). Isso dá uma leitura
# CONTÍNUA: 1.9 e 2.1 de MVRV-Z não caem no mesmo degrau.
#
# Direção: score BAIXO = fundo/barato/oportunidade. Score ALTO = topo/caro.

ESCALAS: dict[str, list[tuple[float, float]]] = {
    # --- Valuation -------------------------------------------------------
    # MVRV Z-Score: <0 marcou todos os fundos de ciclo; 6-8 marcou os topos.
    "mvrv_z":      [(-1.0, 0), (0.0, 10), (1.0, 26), (2.0, 42),
                    (3.0, 56), (4.0, 70), (5.0, 82), (6.5, 93), (8.0, 100)],
    # Proxy: z-score da razão preço/MA200W (mesma leitura, escala própria).
    "z_extensao":  [(-1.5, 0), (-0.8, 12), (-0.2, 26), (0.4, 42),
                    (1.0, 58), (1.6, 72), (2.2, 85), (3.0, 100)],

    # --- Lucro não realizado --------------------------------------------
    # NUPL: <0 capitulação; 0.5 otimismo; >0.75 euforia (topo).
    "nupl":        [(-0.25, 0), (0.0, 12), (0.25, 32), (0.40, 45),
                    (0.50, 58), (0.60, 71), (0.70, 86), (0.75, 93), (0.85, 100)],

    # --- Oferta em lucro (% da oferta) -----------------------------------
    "supply_lucro": [(45, 0), (55, 12), (65, 25), (75, 40),
                     (85, 58), (92, 74), (96, 88), (99, 100)],

    # --- Mãos longas / posição no ciclo ----------------------------------
    # RHODL: usamos log10 (a métrica varia por ordens de grandeza).
    "rhodl_log":   [(2.6, 0), (3.0, 16), (3.4, 34), (3.8, 54),
                    (4.2, 74), (4.5, 89), (5.0, 100)],
    # Fallback: drawdown do topo histórico, em % (0 = no ATH).
    "drawdown":    [(-85, 0), (-70, 10), (-55, 24), (-40, 40),
                    (-28, 55), (-18, 68), (-8, 84), (-2, 96), (0, 100)],

    # --- Realização de lucro ---------------------------------------------
    # SOPR (média de 7 dias): <1 = moedas vendidas no prejuízo (capitulação).
    "sopr":        [(0.95, 0), (0.98, 16), (1.00, 35), (1.010, 50),
                    (1.020, 65), (1.035, 80), (1.050, 92), (1.080, 100)],
    # Fallback: RSI mensal.
    "rsi_mensal":  [(25, 0), (35, 15), (45, 32), (55, 50),
                    (65, 68), (75, 85), (85, 100)],

    # --- Ciclo & sentimento (sempre grátis) ------------------------------
    "mayer":       [(0.6, 0), (0.8, 14), (1.0, 30), (1.3, 46),
                    (1.7, 62), (2.2, 79), (2.8, 92), (3.5, 100)],
    "fng":         [(5, 0), (20, 16), (35, 33), (50, 50),
                    (65, 67), (80, 84), (92, 100)],
    # Relógio do halving: dias desde o último halving (0..1460). O padrão
    # histórico (2012/2016/2020/2024) é topo ~1,5 ano DEPOIS do halving e
    # fundo ~1 ano ANTES do próximo.
    "halving":     [(0, 32), (180, 46), (350, 62), (520, 85), (560, 90),
                    (700, 62), (900, 38), (1100, 18), (1300, 22), (1460, 30)],

    # --- Extras on-chain (entram como reforço quando existem) ------------
    "puell":       [(0.3, 0), (0.5, 14), (0.8, 30), (1.2, 46),
                    (2.0, 66), (3.0, 83), (4.5, 100)],
    # Reserve Risk também em log10 (varia de ~0.0005 a ~0.02).
    "reserve_log": [(-3.4, 0), (-3.1, 16), (-2.8, 34), (-2.5, 54),
                    (-2.2, 74), (-1.9, 90), (-1.6, 100)],
}


def normalizar(chave: str, valor) -> float:
    """
    Converte o valor bruto de uma métrica no sub-score 0..100 da sua escala.
    Valor ausente (None/NaN) devolve NaN — o pilar fica "indisponível" e o
    peso dele é redistribuído entre os pilares que existem.
    """
    if valor is None:
        return float("nan")
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(v):
        return float("nan")
    pontos = ESCALAS.get(chave)
    if not pontos:
        return float("nan")
    xs = [p[0] for p in pontos]
    ys = [float(p[1]) for p in pontos]
    return float(np.interp(v, xs, ys))  # np.interp já "clampa" nas pontas


def normalizar_serie(chave: str, serie: pd.Series) -> pd.Series:
    """Versão vetorizada de `normalizar` para uma série inteira."""
    pontos = ESCALAS.get(chave)
    if not pontos or serie is None or serie.empty:
        return pd.Series(dtype=float)
    xs = [p[0] for p in pontos]
    ys = [float(p[1]) for p in pontos]
    vals = pd.to_numeric(serie, errors="coerce").astype(float)
    out = pd.Series(np.interp(vals.to_numpy(), xs, ys), index=vals.index)
    return out.where(vals.notna())  # NaN entra, NaN sai


# ==========================================================================
# 2) FASES DO CICLO
# ==========================================================================

# (limite_superior, nome, cor)
FASES: list[tuple[float, str, str]] = [
    (15,  "FUNDO PROFUNDO", "#16c784"),
    (35,  "ACUMULAÇÃO",     "#4ade80"),
    (60,  "EXPANSÃO",       "#facc15"),
    (80,  "DISTRIBUIÇÃO",   "#fb923c"),
    (101, "EUFORIA",        "#ef4444"),
]

DESCRICAO_FASE = {
    "FUNDO PROFUNDO": "Mercado abaixo do custo médio da rede. Historicamente "
                      "a faixa de maior retorno futuro — e a mais difícil de "
                      "comprar emocionalmente.",
    "ACUMULAÇÃO": "Saindo do fundo, ainda barato em relação ao ciclo. Fase de "
                  "construir posição com aportes recorrentes.",
    "EXPANSÃO": "Ciclo em desenvolvimento, preço já bem acima do custo médio. "
                "Manter posição, aportes normais, sem pressa de realizar.",
    "DISTRIBUIÇÃO": "Zona historicamente de realização gradual. Lucro não "
                    "realizado alto e mãos longas vendendo para mãos novas.",
    "EUFORIA": "Extremo do ciclo. Historicamente a pior faixa para comprar e a "
               "melhor para ter caixa. Risco de reversão profunda.",
}


def fase_do_score(score: float) -> str:
    """Nome da fase de ciclo para um score 0..100."""
    if score is None or not np.isfinite(score):
        return "—"
    for limite, nome, _cor in FASES:
        if score < limite:
            return nome
    return FASES[-1][1]


def cor_do_score(score: float) -> str:
    """Cor (hex) associada à fase do score — usada no card e nos gráficos."""
    if score is None or not np.isfinite(score):
        return "#8a8f98"
    for limite, _nome, cor in FASES:
        if score < limite:
            return cor
    return FASES[-1][2]


# ==========================================================================
# 3) SÉRIES DERIVADAS DO PREÇO (fallbacks grátis, sem nenhuma chave)
# ==========================================================================

def _serie_preco(preco: pd.DataFrame) -> pd.Series:
    """DataFrame ['date','price'] -> Series de preço indexada por data."""
    s = preco.set_index("date")["price"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def serie_ma200w(preco: pd.DataFrame) -> pd.Series:
    """
    Média móvel de 200 semanas (1400 dias). É o melhor proxy grátis do
    "realized price" (custo médio da rede) — as duas curvas andam coladas
    historicamente. Com histórico curto, exige ao menos 400 dias.
    """
    s = _serie_preco(preco)
    return s.rolling(1400, min_periods=400).mean().rename("ma200w")


def serie_mvrv_proxy(preco: pd.DataFrame) -> pd.Series:
    """MVRV proxy = preço / MA200W (≈ market cap / realized cap)."""
    s = _serie_preco(preco)
    return (s / serie_ma200w(preco)).rename("mvrv_proxy")


def serie_z_extensao(preco: pd.DataFrame) -> pd.Series:
    """
    Proxy do MVRV Z-Score: z-score EXPANDIDO (só com dados do passado, sem
    olhar o futuro) da razão preço/MA200W. Usar `expanding` em vez da série
    inteira evita look-ahead bias no histórico e no backtest.
    """
    r = serie_mvrv_proxy(preco).dropna()
    if r.empty:
        return pd.Series(dtype=float)
    media = r.expanding(min_periods=180).mean()
    desvio = r.expanding(min_periods=180).std()
    return ((r - media) / desvio.replace(0, np.nan)).rename("z_extensao")


def serie_nupl_proxy(preco: pd.DataFrame) -> pd.Series:
    """
    NUPL proxy = 1 − MA200W/preço. Mesma ideia do NUPL real
    (1 − realized cap / market cap), trocando realized price pela MA200W.
    """
    s = _serie_preco(preco)
    ma = serie_ma200w(preco)
    return (1.0 - (ma / s)).rename("nupl_proxy")


def serie_supply_lucro_proxy(preco: pd.DataFrame, janela: int = 1460) -> pd.Series:
    """
    Proxy de "Supply in Profit" (% da oferta em lucro), em 0..100.

    Ideia: supondo que as moedas foram acumuladas de forma parecida ao longo
    do tempo, a fração de moedas em lucro ≈ fração dos ÚLTIMOS `janela` dias
    (4 anos = 1 ciclo) cujo fechamento ficou ABAIXO do preço de hoje.
    """
    s = _serie_preco(preco)
    pct = s.rolling(janela, min_periods=200).apply(
        lambda w: float((w[:-1] < w[-1]).mean() * 100.0), raw=True)
    return pct.rename("supply_lucro_proxy")


def serie_drawdown(preco: pd.DataFrame) -> pd.Series:
    """Drawdown do topo histórico, em % (0 = no ATH, −80 = 80% abaixo)."""
    s = _serie_preco(preco)
    return ((s / s.cummax() - 1.0) * 100.0).rename("drawdown")


def serie_mayer(preco: pd.DataFrame) -> pd.Series:
    """Mayer Multiple = preço / média móvel de 200 dias."""
    s = _serie_preco(preco)
    return (s / s.rolling(200, min_periods=50).mean()).rename("mayer")


def serie_rsi_mensal(preco: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """RSI no timeframe mensal, reindexado para diário (forward-fill)."""
    s = _serie_preco(preco)
    mensal = s.resample("ME").last()
    delta = mensal.diff()
    ganho = delta.clip(lower=0).rolling(periodo, min_periods=periodo).mean()
    perda = (-delta.clip(upper=0)).rolling(periodo, min_periods=periodo).mean()
    rs = ganho / perda.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.reindex(s.index, method="ffill").rename("rsi_mensal")


# --- Relógio do halving ---------------------------------------------------

HALVINGS = [
    pd.Timestamp("2012-11-28"), pd.Timestamp("2016-07-09"),
    pd.Timestamp("2020-05-11"), pd.Timestamp("2024-04-20"),
    pd.Timestamp("2028-04-15"),  # estimativa (ajusta sozinho quando ocorrer)
]


def dias_desde_halving(data) -> float:
    """Dias decorridos desde o último halving anterior à data."""
    d = pd.Timestamp(data).normalize()
    anteriores = [h for h in HALVINGS if h <= d]
    if not anteriores:
        return float("nan")
    return float((d - anteriores[-1]).days)


def serie_halving(indice: pd.DatetimeIndex) -> pd.Series:
    """Série de 'dias desde o halving' para um índice de datas."""
    return pd.Series([dias_desde_halving(d) for d in indice],
                     index=indice, name="halving")


# ==========================================================================
# 4) PILARES — cada linha do card, com fonte on-chain e fallback grátis
# ==========================================================================
# `fontes` é uma lista em ORDEM DE PREFERÊNCIA: usamos a primeira disponível.
# Cada fonte é (chave_da_serie, chave_da_escala, rótulo, tipo).

PILARES: list[dict] = [
    {
        "chave": "valuation", "nome": "MVRV Z-Score", "peso": 0.22,
        "sobre": "Quão esticado o preço está em relação ao custo médio pago "
                 "pela rede (market cap vs realized cap, padronizado).",
        "fontes": [
            ("mvrv_z", "mvrv_z", "MVRV Z-Score", "on-chain"),
            ("z_extensao", "z_extensao", "Z preço/MA200W", "proxy"),
        ],
    },
    {
        "chave": "lucro", "nome": "NUPL", "peso": 0.20,
        "sobre": "Lucro não realizado do mercado inteiro. Negativo = a rede "
                 "está no prejuízo (capitulação); >0.75 = euforia.",
        "fontes": [
            ("nupl", "nupl", "NUPL", "on-chain"),
            ("nupl_proxy", "nupl", "NUPL proxy (MA200W)", "proxy"),
        ],
    },
    {
        "chave": "oferta", "nome": "Supply in Profit", "peso": 0.15,
        "sobre": "% da oferta cujo último movimento foi a um preço menor que "
                 "o atual. Perto de 100% costuma marcar euforia.",
        "fontes": [
            ("supply_lucro", "supply_lucro", "Supply in Profit", "on-chain"),
            ("supply_lucro_proxy", "supply_lucro", "% dias abaixo (4a)", "proxy"),
        ],
    },
    {
        "chave": "hodl", "nome": "RHODL Ratio", "peso": 0.13,
        "sobre": "Peso das moedas jovens contra as antigas. Alto = moedas "
                 "velhas acordando e vendendo para mãos novas (topo).",
        "fontes": [
            ("rhodl_log", "rhodl_log", "RHODL Ratio", "on-chain"),
            ("reserve_log", "reserve_log", "Reserve Risk", "on-chain"),
            ("drawdown", "drawdown", "Drawdown do ATH", "proxy"),
        ],
    },
    {
        "chave": "realizacao", "nome": "SOPR", "peso": 0.10,
        "sobre": "Relação lucro/prejuízo das moedas movidas hoje. <1 = a rede "
                 "está vendendo no prejuízo (capitulação).",
        "fontes": [
            ("sopr", "sopr", "SOPR (7d)", "on-chain"),
            ("rsi_mensal", "rsi_mensal", "RSI mensal", "proxy"),
        ],
    },
    {
        "chave": "ciclo", "nome": "Ciclo & Sentimento", "peso": 0.20,
        "sobre": "Mistura de extensão do preço (Mayer), medo/ganância e o "
                 "relógio do halving — sempre disponível, sem chave.",
        "fontes": [
            ("mayer", "mayer", "Mayer Multiple", "grátis"),
            ("fng", "fng", "Fear & Greed", "grátis"),
            ("halving", "halving", "Relógio do halving", "grátis"),
        ],
        # Este pilar é a MÉDIA das fontes disponíveis (não "a primeira").
        "combinar": "media",
    },
]

# Rótulo curto mostrado à direita de cada barra, por faixa de sub-score.
ROTULOS = {
    "valuation": [(20, "FUNDO"), (40, "SUBVALORIZADO"), (60, "VALOR JUSTO"),
                  (80, "ESTICADO"), (101, "TOPO HISTÓRICO")],
    "lucro":     [(20, "CAPITULAÇÃO"), (40, "ESPERANÇA / MEDO"),
                  (60, "OTIMISMO"), (80, "CRENÇA"), (101, "EUFORIA")],
    "oferta":    [(20, "MAIORIA NO PREJUÍZO"), (40, "ABAIXO DA MÉDIA"),
                  (60, "MÉDIA HISTÓRICA"), (80, "QUASE TUDO EM LUCRO"),
                  (101, "SATURADO")],
    "hodl":      [(20, "FUNDO"), (40, "ACUMULANDO"), (60, "NEUTRO"),
                  (80, "DISTRIBUINDO"), (101, "MÃOS LONGAS SAINDO")],
    "realizacao": [(20, "PREJUÍZO REALIZADO"), (40, "ABAIXO DE 1.0"),
                   (60, "EQUILÍBRIO"), (80, "REALIZANDO LUCRO"),
                   (101, "REALIZAÇÃO EXTREMA")],
    "ciclo":     [(20, "INVERNO"), (40, "PRIMAVERA"), (60, "VERÃO"),
                  (80, "OUTONO"), (101, "SUPERAQUECIDO")],
}


def rotulo_pilar(chave: str, sub: float) -> str:
    """Rótulo curto (ex.: 'VALOR JUSTO') do sub-score de um pilar."""
    if sub is None or not np.isfinite(sub):
        return "INDISPONÍVEL"
    for limite, texto in ROTULOS.get(chave, [(101, "")]):
        if sub < limite:
            return texto
    return ""


# ==========================================================================
# 5) COLETA ON-CHAIN (opcional) — reaproveita o cache do termometro.py
# ==========================================================================

# Nome interno -> candidatos que passamos para `termometro.serie_onchain_cache`.
# Os quatro primeiros usam as MESMAS chaves do termômetro de propósito: assim
# os dois modelos compartilham o cache em disco e a mesma requisição serve aos
# dois (a conta grátis da BGeometrics dá ~15 requisições por dia).
ENDPOINTS_CICLO: dict[str, list[str]] = {
    "mvrv_z":        ["mvrv_z"],           # -> endpoint mvrv-zscore
    "nupl":          ["nupl"],
    "sopr":          ["sopr"],
    "reserve_risk":  ["reserve_risk"],     # -> endpoint reserve-risk
    # Estas duas o termômetro não busca; tentamos os nomes que a API usa.
    "supply_lucro":  ["supply-in-profit", "percent-supply-in-profit"],
    "rhodl":         ["rhodl-ratio", "rhodl"],
}


def tem_onchain() -> bool:
    """True se houver chave da BGeometrics configurada (env ou st.secrets)."""
    return bool(term and term.tem_chave_onchain())


def buscar_series_onchain(metricas: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """
    Busca as séries on-chain do modelo, usando o cache em disco do
    `termometro.py` (que já respeita o limite de requisições da conta grátis).

    Sem chave, devolve {} — e o modelo roda inteiro nos fallbacks grátis.
    Retorna {nome_interno: DataFrame['date','valor']}.
    """
    if not tem_onchain():
        return {}
    alvos = metricas or list(ENDPOINTS_CICLO)
    out: dict[str, pd.DataFrame] = {}
    for nome in alvos:
        for endpoint in ENDPOINTS_CICLO.get(nome, [nome]):
            try:
                serie = term.serie_onchain_cache(endpoint)
            except Exception:
                serie = pd.DataFrame()
            if serie is not None and not serie.empty:
                out[nome] = serie
                break  # candidato funcionou: não gasta requisição com os outros
    return out


def _preparar_onchain(series_onchain: dict | None,
                      indice: pd.DatetimeIndex) -> dict[str, pd.Series]:
    """
    Alinha as séries on-chain ao índice diário do preço e aplica as
    transformações que as escalas esperam (log10 no RHODL e no Reserve Risk,
    média de 7 dias no SOPR, % no supply).
    """
    prontas: dict[str, pd.Series] = {}
    for nome, df in (series_onchain or {}).items():
        if df is None or len(df) == 0:
            continue
        s = (df.set_index("date")["valor"].astype(float)
             .sort_index().reindex(indice, method="ffill"))
        if nome == "rhodl":
            prontas["rhodl_log"] = np.log10(s.where(s > 0))
        elif nome == "reserve_risk":
            prontas["reserve_log"] = np.log10(s.where(s > 0))
        elif nome == "sopr":
            prontas["sopr"] = s.rolling(7, min_periods=1).mean()
        elif nome == "supply_lucro":
            # A API pode devolver 0..1 (fração) ou 0..100 (%). Normalizamos.
            prontas["supply_lucro"] = s * 100.0 if s.dropna().max() <= 1.5 else s
        else:
            prontas[nome] = s
    return prontas


# ==========================================================================
# 6) O MODELO — séries de todos os componentes e o score composto
# ==========================================================================

def montar_series(preco: pd.DataFrame, fng: pd.DataFrame | None = None,
                  series_onchain: dict | None = None) -> dict[str, pd.Series]:
    """
    Monta TODAS as séries de componentes (grátis + on-chain) alinhadas ao
    índice diário do preço. É a base tanto do snapshot de hoje quanto do
    histórico do score.
    """
    s = _serie_preco(preco)
    idx = s.index

    series: dict[str, pd.Series] = {
        "z_extensao": serie_z_extensao(preco).reindex(idx),
        "nupl_proxy": serie_nupl_proxy(preco).reindex(idx),
        "supply_lucro_proxy": serie_supply_lucro_proxy(preco).reindex(idx),
        "drawdown": serie_drawdown(preco).reindex(idx),
        "rsi_mensal": serie_rsi_mensal(preco).reindex(idx),
        "mayer": serie_mayer(preco).reindex(idx),
        "halving": serie_halving(idx),
    }
    if fng is not None and len(fng) > 0 and "fng" in getattr(fng, "columns", []):
        series["fng"] = (fng.set_index("date")["fng"].astype(float)
                         .sort_index().reindex(idx, method="ffill"))

    series.update(_preparar_onchain(series_onchain, idx))
    return series


def _sub_score_pilar(pilar: dict, series: dict[str, pd.Series]
                     ) -> tuple[pd.Series, list[dict]]:
    """
    Sub-score 0..100 de um pilar ao longo do tempo + quais fontes entraram.

    - Pilares normais: usam a PRIMEIRA fonte disponível (on-chain > proxy),
      completando buracos com as seguintes (`combine_first`).
    - Pilar com `combinar='media'`: média das fontes disponíveis.
    """
    usadas: list[dict] = []
    normalizadas: list[pd.Series] = []

    for chave_serie, chave_escala, rotulo, tipo in pilar["fontes"]:
        bruta = series.get(chave_serie)
        if bruta is None or bruta.dropna().empty:
            continue
        norm = normalizar_serie(chave_escala, bruta)
        if norm.dropna().empty:
            continue
        normalizadas.append(norm)
        usadas.append({"serie": chave_serie, "escala": chave_escala,
                       "rotulo": rotulo, "tipo": tipo})

    if not normalizadas:
        return pd.Series(dtype=float), []

    if pilar.get("combinar") == "media":
        sub = pd.concat(normalizadas, axis=1).mean(axis=1, skipna=True)
    else:
        sub = normalizadas[0]
        for outra in normalizadas[1:]:
            sub = sub.combine_first(outra)
    return sub, usadas


def serie_score(preco: pd.DataFrame, fng: pd.DataFrame | None = None,
                series_onchain: dict | None = None,
                pesos: dict[str, float] | None = None) -> pd.DataFrame:
    """
    Score de ciclo (0..100) AO LONGO DO TEMPO.

    Média ponderada dos pilares disponíveis em cada data: se um pilar não
    tem dado naquele dia, o peso dele é redistribuído entre os demais (o
    score continua na mesma escala 0..100).

    Retorna DataFrame ['date','price','score', <um coluna por pilar>].
    """
    s = _serie_preco(preco)
    series = montar_series(preco, fng, series_onchain)

    num = pd.Series(0.0, index=s.index)
    den = pd.Series(0.0, index=s.index)
    colunas: dict[str, pd.Series] = {}

    for pilar in PILARES:
        sub, usadas = _sub_score_pilar(pilar, series)
        if sub.empty or not usadas:
            continue
        sub = sub.reindex(s.index)
        peso = float((pesos or {}).get(pilar["chave"], pilar["peso"]))
        disponivel = sub.notna()
        num = num.add((sub.fillna(0.0) * peso).where(disponivel, 0.0), fill_value=0.0)
        den = den.add(pd.Series(peso, index=s.index).where(disponivel, 0.0),
                      fill_value=0.0)
        colunas[pilar["chave"]] = sub

    score = (num / den.replace(0, np.nan)).rename("score")
    out = pd.DataFrame({"price": s, "score": score})
    for chave, sub in colunas.items():
        out[chave] = sub
    out = out.dropna(subset=["score"]).reset_index()
    out = out.rename(columns={out.columns[0]: "date"})
    return out


def calcular(preco: pd.DataFrame, fng_atual: float | None = None,
             fng: pd.DataFrame | None = None,
             series_onchain: dict | None = None,
             pesos: dict[str, float] | None = None) -> dict:
    """
    Snapshot do modelo HOJE: score, fase, composição por pilar e plano de
    posição. É o que alimenta o card visual.

    - `preco`: DataFrame ['date','price'] (quanto mais histórico, melhor:
      a MA200W precisa de ~4 anos para ficar completa).
    - `fng_atual` / `fng`: Fear & Greed (valor de hoje e/ou série histórica).
    - `series_onchain`: {metrica: DataFrame['date','valor']}; se None e houver
      chave configurada, busca sozinho (com cache).

    Retorna dict com: score, fase, cor, descricao, componentes[], plano{},
    cobertura (% do peso com dado), fonte ('on-chain' ou 'proxy'), data.
    """
    if preco is None or len(preco) < 60:
        raise ValueError("Preciso de pelo menos ~60 dias de preço para o modelo.")

    # Fear & Greed: aceita série histórica, valor de hoje, ou os dois.
    fng_df = fng
    if fng_df is None and fng_atual is not None:
        ultima = pd.Timestamp(preco["date"].iloc[-1]).normalize()
        fng_df = pd.DataFrame({"date": [ultima], "fng": [float(fng_atual)]})

    if series_onchain is None:
        series_onchain = buscar_series_onchain()

    s = _serie_preco(preco)
    series = montar_series(preco, fng_df, series_onchain)

    componentes, num, den, peso_total = [], 0.0, 0.0, 0.0
    tem_dado_onchain = False

    for pilar in PILARES:
        peso = float((pesos or {}).get(pilar["chave"], pilar["peso"]))
        peso_total += peso
        sub_serie, usadas = _sub_score_pilar(pilar, series)
        sub = float(sub_serie.dropna().iloc[-1]) if not sub_serie.dropna().empty \
            else float("nan")

        # Valor bruto e rótulo da fonte efetivamente usada hoje.
        fonte_nome, fonte_tipo, valor = "—", "—", float("nan")
        for u in usadas:
            bruta = series.get(u["serie"])
            if bruta is not None and not bruta.dropna().empty:
                fonte_nome, fonte_tipo = u["rotulo"], u["tipo"]
                valor = float(bruta.dropna().iloc[-1])
                break
        if fonte_tipo == "on-chain":
            tem_dado_onchain = True

        ok = np.isfinite(sub)
        if ok:
            num += sub * peso
            den += peso

        componentes.append({
            "chave": pilar["chave"], "nome": pilar["nome"], "peso": peso,
            "sub_score": sub if ok else float("nan"),
            "valor": valor, "fonte": fonte_nome, "tipo": fonte_tipo,
            "rotulo": rotulo_pilar(pilar["chave"], sub),
            "sobre": pilar["sobre"], "ok": bool(ok),
            "fontes_usadas": usadas,
        })

    score = (num / den) if den > 0 else float("nan")
    fase = fase_do_score(score)

    # Momentum do ciclo: variação do score em 30 dias (usa o histórico).
    delta30 = float("nan")
    try:
        hist = serie_score(preco, fng_df, series_onchain, pesos)
        if len(hist) > 31:
            delta30 = float(hist["score"].iloc[-1] - hist["score"].iloc[-31])
    except Exception:
        pass

    return {
        "score": score,
        "fase": fase,
        "cor": cor_do_score(score),
        "descricao": DESCRICAO_FASE.get(fase, ""),
        "componentes": componentes,
        "plano": plano_posicao(score),
        "cobertura": (den / peso_total * 100.0) if peso_total else 0.0,
        "fonte": "on-chain" if tem_dado_onchain else "proxy",
        "delta30": delta30,
        "preco": float(s.iloc[-1]),
        "data": pd.Timestamp(s.index[-1]).to_pydatetime(),
    }


# ==========================================================================
# 7) GESTÃO DE POSIÇÃO — traduz o score em ação (o objetivo do modelo)
# ==========================================================================

# Curva de exposição-alvo: quanto do patrimônio de cripto ficaria em BTC
# para cada nível de score. Contínua, sem "tudo ou nada".
CURVA_EXPOSICAO = [(0, 100), (15, 100), (30, 92), (45, 80), (60, 62),
                   (70, 45), (80, 30), (90, 16), (100, 8)]

# Multiplicador do aporte recorrente (DCA adaptativo): compra-se MAIS quando
# o ciclo está barato e MENOS quando está caro.
CURVA_DCA = [(0, 3.0), (15, 2.5), (30, 1.8), (45, 1.2), (60, 0.8),
             (70, 0.5), (80, 0.25), (90, 0.0), (100, 0.0)]


def alvo_exposicao(score: float) -> float:
    """Exposição-alvo em BTC (%) para um score de ciclo."""
    if score is None or not np.isfinite(score):
        return float("nan")
    xs = [p[0] for p in CURVA_EXPOSICAO]
    ys = [float(p[1]) for p in CURVA_EXPOSICAO]
    return float(np.interp(score, xs, ys))


def multiplicador_dca(score: float) -> float:
    """Quantas vezes o aporte-base comprar neste ponto do ciclo."""
    if score is None or not np.isfinite(score):
        return float("nan")
    xs = [p[0] for p in CURVA_DCA]
    ys = [float(p[1]) for p in CURVA_DCA]
    return round(float(np.interp(score, xs, ys)), 2)


def plano_posicao(score: float) -> dict:
    """
    Traduz o score num plano prático e sem emoção:

      - `exposicao_alvo`: % da carteira cripto em BTC;
      - `realizado_alvo`: % da posição já realizada (o complemento da curva);
      - `dca`: multiplicador do aporte recorrente;
      - `acao` / `detalhe`: a frase que resume o que fazer agora.

    Nada disso é recomendação financeira — é a mecânica do modelo, para o
    usuário decidir com regra em vez de emoção.
    """
    if score is None or not np.isfinite(score):
        return {"exposicao_alvo": float("nan"), "realizado_alvo": float("nan"),
                "dca": float("nan"), "acao": "—", "detalhe": "Dados insuficientes."}

    exp = alvo_exposicao(score)
    dca = multiplicador_dca(score)
    fase = fase_do_score(score)

    if fase == "FUNDO PROFUNDO":
        acao = "ACUMULAR AGRESSIVO"
        detalhe = (f"Aporte de {dca:.1f}× o normal. Manter ~{exp:.0f}% em BTC. "
                   "Zona de maior desconforto e maior retorno histórico.")
    elif fase == "ACUMULAÇÃO":
        acao = "ACUMULAR"
        detalhe = (f"Aporte de {dca:.1f}× o normal, exposição-alvo ~{exp:.0f}%. "
                   "Construir posição sem pressa, em parcelas.")
    elif fase == "EXPANSÃO":
        acao = "MANTER"
        detalhe = (f"Aporte de {dca:.1f}× (normal/reduzido) e alvo ~{exp:.0f}%. "
                   "Deixar o ciclo trabalhar; ainda cedo para realizar.")
    elif fase == "DISTRIBUIÇÃO":
        acao = "REALIZAR GRADUAL"
        detalhe = (f"Reduzir para ~{exp:.0f}% em BTC ({100 - exp:.0f}% já "
                   "realizado), em parcelas a cada alta do score. Aporte "
                   f"{dca:.2f}×.")
    else:  # EUFORIA
        acao = "REALIZAR / CAIXA"
        detalhe = (f"Alvo ~{exp:.0f}% em BTC. Historicamente a pior faixa para "
                   "comprar. Parar aportes e concluir a realização planejada.")

    return {"exposicao_alvo": exp, "realizado_alvo": 100.0 - exp, "dca": dca,
            "acao": acao, "detalhe": detalhe}


def serie_semanal(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Score em fechamento SEMANAL (segunda a domingo).

    O score diário oscila com ruído de preço; o modelo é de CICLO, então a
    leitura semanal é a que deve guiar decisão (uma decisão por semana, no
    máximo). Retorna ['date','price','score','fase'].
    """
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["date", "price", "score", "fase"])
    df = hist.set_index("date").sort_index()
    sem = df[["price", "score"]].resample("W-SUN").last().dropna()
    sem["fase"] = sem["score"].map(fase_do_score)
    sem = sem.reset_index()
    # A semana em curso ainda não fechou: rotula pela última data que existe
    # de fato, para não plotar um ponto no futuro.
    ultima = df.index.max()
    sem["date"] = sem["date"].where(sem["date"] <= ultima, ultima)
    return sem


# ==========================================================================
# 8) BACKTEST — a curva de exposição vs. comprar e segurar
# ==========================================================================

def backtest_exposicao(hist: pd.DataFrame, semanal: bool = True) -> dict:
    """
    Testa a mecânica do modelo: em vez de LONG/CAIXA (tudo ou nada), a
    posição segue a CURVA de exposição-alvo do score.

    Sem look-ahead: a exposição de ontem rende o retorno de hoje. Com
    `semanal=True`, a exposição só muda no fechamento de semana (mais
    realista e com muito menos giro).

    Retorna {'curva': DataFrame, 'modelo': métricas, 'hold': métricas}.
    """
    if hist is None or hist.empty or len(hist) < 120:
        return {}

    df = hist.sort_values("date").reset_index(drop=True).copy()
    df["ret"] = df["price"].pct_change().fillna(0.0)
    df["exposicao"] = df["score"].map(alvo_exposicao) / 100.0

    if semanal:
        # Congela a exposição: durante a semana vale o alvo do FECHAMENTO da
        # semana anterior (uma decisão por semana, sem espiar o futuro).
        semanas = pd.Series(df["date"]).dt.to_period("W")
        fecha_semana = df.groupby(semanas)["exposicao"].last().shift(1)
        df["exposicao"] = semanas.map(fecha_semana).astype(float)
        df["exposicao"] = df["exposicao"].ffill().fillna(0.0)

    pos = df["exposicao"].shift(1).fillna(0.0)  # decisão de ontem, retorno de hoje
    df["ret_modelo"] = df["ret"] * pos
    df["cap_modelo"] = (1 + df["ret_modelo"]).cumprod()
    df["cap_hold"] = (1 + df["ret"]).cumprod()
    df["pos"] = pos

    def _metricas(cap: pd.Series, rets: pd.Series) -> dict:
        dias = (df["date"].iloc[-1] - df["date"].iloc[0]).days or 1
        anos = dias / 365.25
        final = float(cap.iloc[-1])
        pico = cap.cummax()
        sd = float(rets.std())
        return {
            "ret_total": final - 1.0,
            "cagr": (final ** (1 / anos) - 1.0) if anos > 0 else float("nan"),
            "dd_max": float((cap / pico - 1).min()),
            "sharpe": (float(rets.mean()) / sd * np.sqrt(365)) if sd > 0
            else float("nan"),
        }

    met_modelo = _metricas(df["cap_modelo"], df["ret_modelo"])
    met_modelo["exposicao_media"] = float(pos.mean() * 100)
    return {
        "curva": df[["date", "price", "score", "pos", "cap_modelo", "cap_hold"]],
        "modelo": met_modelo,
        "hold": _metricas(df["cap_hold"], df["ret"]),
    }


# ==========================================================================
# 9) CARD VISUAL (HTML/SVG puro) — o "indicador" propriamente dito
# ==========================================================================
# Sem dependência de JS ou biblioteca: é HTML+SVG inline, então funciona
# dentro do Streamlit (st.markdown com unsafe_allow_html) e também como um
# arquivo .html sozinho (exportado pelo script 06).

import html as _html  # noqa: E402  (usado só na camada de apresentação)

MESES_PT = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
            "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def _ponto_arco(cx: float, cy: float, r: float, graus: float) -> tuple[float, float]:
    """Ponto (x, y) sobre um arco, em graus (180° = esquerda, 0° = direita)."""
    rad = np.radians(graus)
    return cx + r * np.cos(rad), cy - r * np.sin(rad)


def _svg_gauge(score: float, cor: str, fase: str) -> str:
    """Medidor semicircular do score (SVG inline, sem JS)."""
    valido = score is not None and np.isfinite(score)
    p = float(np.clip(score, 0, 100)) if valido else 0.0
    cx, cy, r = 150.0, 158.0, 108.0
    ang = 180.0 - 1.8 * p                      # 0 -> 180°, 100 -> 0°
    x0, y0 = _ponto_arco(cx, cy, r, 180)
    x1, y1 = _ponto_arco(cx, cy, r, 0)
    xp, yp = _ponto_arco(cx, cy, r, ang)
    # Ponteiro: uma linha curta do centro até o ponto do arco.
    xi, yi = _ponto_arco(cx, cy, r * 0.62, ang)
    grande = 0  # semicírculo nunca passa de 180°

    marcas = []
    for valor in (0, 25, 50, 75, 100):
        tx, ty = _ponto_arco(cx, cy, r + 22, 180 - 1.8 * valor)
        marcas.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
            f'fill="#5c646e" font-size="11" font-family="inherit">{valor}</text>')

    return f"""
<svg viewBox="0 0 300 210" width="100%" style="max-width:320px;display:block;margin:0 auto">
  <defs>
    <filter id="bcm-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path d="M {x0:.1f} {y0:.1f} A {r} {r} 0 0 1 {x1:.1f} {y1:.1f}"
        fill="none" stroke="#20262c" stroke-width="16" stroke-linecap="round"/>
  {"" if not valido else f'''
  <path d="M {x0:.1f} {y0:.1f} A {r} {r} 0 {grande} 1 {xp:.1f} {yp:.1f}"
        fill="none" stroke="{cor}" stroke-width="16" stroke-linecap="round"
        filter="url(#bcm-glow)" opacity="0.95"/>
  <line x1="{xi:.1f}" y1="{yi:.1f}" x2="{xp:.1f}" y2="{yp:.1f}"
        stroke="{cor}" stroke-width="2.5" stroke-linecap="round" opacity="0.9"/>
  <circle cx="{xp:.1f}" cy="{yp:.1f}" r="7" fill="{cor}" filter="url(#bcm-glow)"/>
  '''}
  {''.join(marcas)}
  <text x="{cx - 8}" y="{cy - 14}" text-anchor="middle" fill="{cor}"
        font-size="62" font-weight="800" font-family="inherit"
        letter-spacing="-2">{f"{p:.0f}" if valido else "--"}</text>
  <text x="{cx + 46}" y="{cy - 14}" text-anchor="start" fill="#6b7480"
        font-size="15" font-family="inherit">/100</text>
  <text x="{cx}" y="{cy + 14}" text-anchor="middle" fill="#9aa3ad"
        font-size="12" font-weight="600" letter-spacing="2"
        font-family="inherit">{_html.escape(fase)}</text>
</svg>"""


def _linha_componente(comp: dict) -> str:
    """Uma linha da 'composição do score' (nome + barra + rótulo)."""
    ok = comp.get("ok")
    sub = comp.get("sub_score", float("nan"))
    largura = float(np.clip(sub, 0, 100)) if ok else 0.0
    cor = cor_do_score(sub) if ok else "#3a4149"
    tipo = comp.get("tipo", "")
    selo = ("" if tipo == "on-chain" else
            f'<span class="bcm-tag">{_html.escape(str(tipo))}</span>')
    titulo = f'{comp.get("fonte", "")} · {comp.get("sobre", "")}'
    return f"""
  <div class="bcm-row" title="{_html.escape(titulo)}">
    <div class="bcm-row-nome">{_html.escape(comp.get("nome", ""))}{selo}</div>
    <div class="bcm-track"><div class="bcm-fill" style="width:{largura:.1f}%;
         background:{cor};box-shadow:0 0 12px {cor}66"></div></div>
    <div class="bcm-row-rot">{_html.escape(comp.get("rotulo", ""))}</div>
  </div>"""


def _barra_fases(score: float) -> str:
    """Espectro das 5 fases com o marcador na posição do score."""
    valido = score is not None and np.isfinite(score)
    p = float(np.clip(score, 0, 100)) if valido else 0.0
    fase_atual = fase_do_score(score)
    rotulos = []
    for limite, nome, _cor in FASES:
        ativo = "bcm-fase-on" if nome == fase_atual else ""
        seta = "▲ " if nome == fase_atual else ""
        rotulos.append(f'<div class="bcm-fase {ativo}">{seta}{nome}</div>')
    marcador = ("" if not valido else
                f'<div class="bcm-marcador" style="left:{p:.1f}%"></div>')
    return f"""
  <div class="bcm-espectro">
    <div class="bcm-espectro-bar">{marcador}</div>
    <div class="bcm-fases">{''.join(rotulos)}</div>
  </div>"""


CSS_CARD = """
<style>
.bcm-card{background:#0b0e11;border:1px solid #1b2127;border-radius:16px;
  padding:22px 26px 18px;color:#e6e9ec;font-family:'Inter',system-ui,
  -apple-system,'Segoe UI',sans-serif;
  background-image:radial-gradient(#161b21 1px,transparent 1px);
  background-size:22px 22px;}
.bcm-top{display:flex;align-items:center;justify-content:space-between;gap:12px;
  border-bottom:1px solid #1b2127;padding-bottom:14px;flex-wrap:wrap}
.bcm-marca{display:flex;align-items:center;gap:12px}
.bcm-btc{width:34px;height:34px;border-radius:50%;background:
  radial-gradient(circle at 30% 25%,#ffb347,#f7931a);color:#111;font-weight:800;
  display:flex;align-items:center;justify-content:center;font-size:19px;
  box-shadow:0 0 18px #f7931a55}
.bcm-titulo{font-size:14px;font-weight:700;letter-spacing:4px}
.bcm-pill{display:flex;align-items:center;gap:8px;border-radius:999px;
  padding:6px 14px;font-size:11px;font-weight:700;letter-spacing:2px}
.bcm-dot{width:8px;height:8px;border-radius:50%}
.bcm-ver{border:1px solid #2a3138;border-radius:999px;padding:5px 10px;
  font-size:10px;color:#7c858f;letter-spacing:1px}
.bcm-sub{color:#79828d;font-size:12px;margin:12px 0 18px;font-style:italic}
.bcm-corpo{display:flex;gap:26px;flex-wrap:wrap}
.bcm-col-esq{flex:1 1 260px;min-width:240px;text-align:center}
.bcm-col-dir{flex:2 1 380px;min-width:300px}
.bcm-legenda{font-size:10px;letter-spacing:3px;color:#6b7480;margin-bottom:14px}
.bcm-row{display:flex;align-items:center;gap:14px;background:#12171c;
  border:1px solid #1b2127;border-radius:10px;padding:10px 14px;margin-bottom:8px}
.bcm-row-nome{flex:0 0 180px;font-size:12.5px;font-weight:600;display:flex;
  align-items:center;gap:6px}
.bcm-tag{font-size:8px;letter-spacing:1px;color:#7c858f;border:1px solid #2a3138;
  border-radius:4px;padding:1px 4px;text-transform:uppercase}
.bcm-track{flex:1;height:7px;border-radius:99px;background:#20262c;overflow:hidden}
.bcm-fill{height:100%;border-radius:99px}
.bcm-row-rot{flex:0 0 142px;text-align:right;font-size:9px;letter-spacing:1.4px;
  color:#8b939d}
.bcm-espectro{margin-top:26px}
.bcm-espectro-bar{position:relative;height:6px;border-radius:99px;background:
  linear-gradient(90deg,#16c784 0%,#4ade80 18%,#facc15 45%,#fb923c 72%,#ef4444 100%)}
.bcm-marcador{position:absolute;top:-5px;width:3px;height:16px;border-radius:2px;
  background:#fff;box-shadow:0 0 10px #fff;transform:translateX(-50%)}
.bcm-marcador:after{content:'';position:absolute;top:-7px;left:50%;width:9px;
  height:9px;border-radius:50%;background:#fff;transform:translateX(-50%)}
.bcm-fases{display:flex;justify-content:space-between;margin-top:12px;gap:6px}
.bcm-fase{font-size:9px;letter-spacing:1.6px;color:#5c646e;flex:1;text-align:center}
.bcm-fase:first-child{text-align:left}.bcm-fase:last-child{text-align:right}
.bcm-fase-on{color:#fff;font-weight:700}
.bcm-rodape{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;
  border-top:1px solid #1b2127;margin-top:20px;padding-top:14px;
  font-size:9px;letter-spacing:2px;color:#6b7480}
.bcm-rodape b{color:#aab2bc;font-weight:600}
.bcm-plano{background:#12171c;border:1px solid #1b2127;border-left:3px solid;
  border-radius:10px;padding:12px 16px;margin-top:18px}
.bcm-plano-acao{font-size:13px;font-weight:700;letter-spacing:1px}
.bcm-plano-txt{font-size:11.5px;color:#98a1ab;margin-top:4px;line-height:1.5}
</style>
"""


def card_html(res: dict, incluir_css: bool = True, versao: str = "v1") -> str:
    """
    Monta o card do BTC Cycle Model em HTML+SVG a partir do dict de
    `calcular()`. Use `incluir_css=False` se o CSS já estiver na página.
    """
    score = res.get("score", float("nan"))
    cor = res.get("cor", "#8a8f98")
    fase = res.get("fase", "—")
    plano = res.get("plano", {})
    data = res.get("data")
    carimbo = ""
    if data is not None:
        d = pd.Timestamp(data)
        carimbo = f"{MESES_PT[d.month - 1]} / {d.year}"

    linhas = "".join(_linha_componente(c) for c in res.get("componentes", []))
    cobertura = res.get("cobertura", 0.0)
    fonte = res.get("fonte", "proxy")
    delta = res.get("delta30", float("nan"))
    seta = "" if not np.isfinite(delta) else (
        f" · <b>{delta:+.0f}</b> EM 30D")

    css = CSS_CARD if incluir_css else ""
    return f"""{css}
<div class="bcm-card">
  <div class="bcm-top">
    <div class="bcm-marca">
      <div class="bcm-btc">₿</div>
      <div class="bcm-titulo">BTC CYCLE MODEL</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="bcm-pill" style="background:{cor}1f;color:{cor};
           border:1px solid {cor}44">
        <span class="bcm-dot" style="background:{cor};box-shadow:0 0 8px {cor}">
        </span>{_html.escape(fase)}
      </div>
      <div class="bcm-ver">{_html.escape(versao)}</div>
    </div>
  </div>
  <div class="bcm-sub">Gestão de posição baseada em ciclos de mercado ·
     sem emoção, sem adivinhação</div>
  <div class="bcm-corpo">
    <div class="bcm-col-esq">
      <div class="bcm-legenda">COMPOSITE SCORE</div>
      {_svg_gauge(score, cor, fase)}
    </div>
    <div class="bcm-col-dir">
      <div class="bcm-legenda">COMPOSIÇÃO DO SCORE</div>
      {linhas}
    </div>
  </div>
  {_barra_fases(score)}
  <div class="bcm-plano" style="border-left-color:{cor}">
    <div class="bcm-plano-acao" style="color:{cor}">
      {_html.escape(str(plano.get("acao", "—")))}</div>
    <div class="bcm-plano-txt">{_html.escape(str(plano.get("detalhe", "")))}</div>
  </div>
  <div class="bcm-rodape">
    <div>DCA ADAPTATIVO <b>{plano.get('dca', float('nan')):.2f}×</b></div>
    <div>EXPOSIÇÃO-ALVO <b>{plano.get('exposicao_alvo', float('nan')):.0f}%</b></div>
    <div>REALIZADO <b>{plano.get('realizado_alvo', float('nan')):.0f}%</b></div>
    <div>FONTE <b>{_html.escape(fonte.upper())}</b> · {cobertura:.0f}% DO PESO{seta}</div>
    <div>{carimbo}</div>
  </div>
</div>"""


def pagina_html(res: dict, titulo: str = "BTC Cycle Model") -> str:
    """Documento HTML completo com o card (para exportar/abrir no navegador)."""
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(titulo)}</title>
<style>body{{background:#07090b;margin:0;padding:28px;
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif}}
.wrap{{max-width:1120px;margin:0 auto}}
.aviso{{color:#5c646e;font-size:11px;margin-top:16px;text-align:center}}</style>
</head><body><div class="wrap">
{card_html(res)}
<div class="aviso">Conteúdo educativo — não é recomendação financeira.</div>
</div></body></html>"""


# ==========================================================================
# 10) AUTOTESTE — roda offline, com uma série sintética (não precisa de rede)
# ==========================================================================

def _preco_sintetico(anos: int = 9, semente: int = 7) -> pd.DataFrame:
    """
    Gera um preço fake com ciclos de ~4 anos + tendência + ruído. Serve para
    testar a mecânica do modelo sem depender de API nenhuma.
    """
    rng = np.random.default_rng(semente)
    n = anos * 365
    datas = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="D")
    t = np.arange(n)
    tendencia = 0.0016 * t                                  # alta de longo prazo
    ciclo = 0.95 * np.sin(2 * np.pi * (t - 200) / 1460)     # ciclo de 4 anos
    ruido = np.cumsum(rng.normal(0, 0.012, n)) * 0.25
    preco = 3000 * np.exp(tendencia + ciclo + ruido)
    return pd.DataFrame({"date": datas, "price": preco})


def _autoteste() -> int:
    """Checagens de sanidade do modelo. Retorna 0 se tudo passar."""
    falhas = []

    def checa(cond, msg):
        print(("  ok   " if cond else "  FALHA ") + msg)
        if not cond:
            falhas.append(msg)

    print("BTC Cycle Model — autoteste (série sintética, sem rede)\n")
    preco = _preco_sintetico()

    # --- escalas -------------------------------------------------------
    checa(normalizar("mvrv_z", -3) == 0.0, "escala trava no piso (MVRV-Z muito baixo -> 0)")
    checa(normalizar("mvrv_z", 99) == 100.0, "escala trava no teto (MVRV-Z altíssimo -> 100)")
    checa(normalizar("nupl", 0.0) < normalizar("nupl", 0.7), "NUPL é monotônico")
    checa(np.isnan(normalizar("sopr", None)), "valor ausente vira NaN")

    # --- snapshot ------------------------------------------------------
    res = calcular(preco, fng_atual=50.0, series_onchain={})
    checa(0 <= res["score"] <= 100, f"score dentro de 0..100 (={res['score']:.1f})")
    checa(res["fase"] in [f[1] for f in FASES], f"fase válida ({res['fase']})")
    checa(len(res["componentes"]) == len(PILARES), "um componente por pilar")
    checa(all(c["tipo"] in ("on-chain", "proxy", "grátis", "—")
              for c in res["componentes"]), "todo componente declara sua fonte")
    checa(res["cobertura"] > 60, f"cobertura de peso razoável sem chave "
                                 f"({res['cobertura']:.0f}%)")

    # --- histórico -----------------------------------------------------
    hist = serie_score(preco, series_onchain={})
    checa(len(hist) > 500, f"histórico do score calculado ({len(hist)} dias)")
    checa(hist["score"].between(0, 100).all(), "histórico inteiro dentro de 0..100")

    # O score tem que ser MAIOR nos topos do que nos fundos do preço.
    topo = hist.loc[hist["price"].idxmax()]
    fundo = hist.loc[hist.loc[hist["date"] > hist["date"].min() +
                              pd.Timedelta(days=500), "price"].idxmin()]
    checa(topo["score"] > fundo["score"],
          f"score no topo ({topo['score']:.0f}) > no fundo ({fundo['score']:.0f})")

    # --- plano de posição ----------------------------------------------
    checa(alvo_exposicao(5) > alvo_exposicao(50) > alvo_exposicao(95),
          "exposição-alvo cai conforme o ciclo esquenta")
    checa(multiplicador_dca(5) > multiplicador_dca(65),
          "DCA compra mais no fundo do que no topo")
    checa(plano_posicao(90)["acao"] == "REALIZAR / CAIXA",
          "euforia manda realizar")

    # --- semanal + backtest --------------------------------------------
    sem = serie_semanal(hist)
    checa(len(sem) > 100 and "fase" in sem.columns, "série semanal montada")
    bt = backtest_exposicao(hist)
    checa(bool(bt) and "modelo" in bt, "backtest roda")
    if bt:
        checa(bt["modelo"]["dd_max"] >= bt["hold"]["dd_max"] - 1e-9,
              f"drawdown do modelo ({bt['modelo']['dd_max']:.1%}) não é pior "
              f"que o do buy & hold ({bt['hold']['dd_max']:.1%})")

    # --- render --------------------------------------------------------
    html_card = card_html(res)
    checa("bcm-card" in html_card and "<svg" in html_card, "card HTML renderiza")
    checa(str(int(round(res["score"]))) in html_card, "score aparece no card")

    print(f"\n{'TUDO OK' if not falhas else f'{len(falhas)} FALHA(S)'} — "
          f"score sintético de hoje: {res['score']:.1f} ({res['fase']})")
    return 0 if not falhas else 1


if __name__ == "__main__":
    import sys as _sys
    if "--autoteste" in _sys.argv:
        raise SystemExit(_autoteste())
    print(__doc__)
