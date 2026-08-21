# 📋 Referência de Dados — BTC Mood Tracker → reaproveitar no Telegram Bot

Resumo técnico de TODAS as fontes de dados, endpoints, formatos e da lógica do
"Termômetro" (score consolidado), para reusar em outro programa (ex.: bot de
Telegram). Tudo gratuito; só o on-chain (BGeometrics) usa uma **chave grátis**.

> ⚠️ Não inclua tokens no código. Use variável de ambiente / secret.
> Conteúdo educativo — **não é recomendação financeira.**

---

## 1) Preço do BTC (grátis, sem chave) — com fallback em cascata

Use a cascata: tenta Binance → CryptoCompare → CoinGecko. Em datacenter
(nuvem), Binance costuma dar 451 e Reddit 429; CryptoCompare/CoinGecko funcionam.

| Fonte | Endpoint | Observação |
|-------|----------|-----------|
| **Binance** | `GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime=<ms>&limit=1000` | Pagine por `startTime`. Campo de preço = índice 4 (close). Bloqueia EUA (451). |
| **CryptoCompare** | `GET https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=2000` | Resposta: `Data.Data[]` com `{time(epoch s), close}`. ~2000 dias. Libera datacenter. |
| **CoinGecko** | `GET https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365` | Resposta: `prices[] = [ [ts_ms, preço], ... ]`. Tier grátis ~365 dias. |

Header recomendado em todas: `User-Agent: <algo>/1.0`.

---

## 2) Humor / sentimento (grátis, sem chave)

| Sinal | Endpoint | Resposta | Escala |
|-------|----------|----------|--------|
| **Fear & Greed** | `GET https://api.alternative.me/fng/?limit=0&format=json` | `data[] = {timestamp(epoch s, string), value("0".."100")}` | 0 = medo extremo, 100 = ganância |
| **Google Trends** | via lib `pytrends` (termo "Bitcoin", `timeframe="today 5-y"`) | série semanal 0–100 | OPCIONAL; toma rate limit (429) com facilidade |
| **Reddit** (texto p/ IA) | `GET https://www.reddit.com/r/<sub>/new.json?limit=100` | `data.children[].data = {title, selftext, created_utc}` | **Precisa** header `User-Agent`; bloqueia datacenter |
| **CryptoCompare News** (fallback p/ IA) | `GET https://min-api.cryptocompare.com/data/v2/news/?lang=EN` | `Data[] = {title, body, published_on(epoch s), source}` | Libera datacenter; use quando Reddit falhar |

---

## 3) On-chain — BGeometrics / bitcoin-data.com (chave GRÁTIS)

- **Base:** `https://api.bgeometrics.com/v1/<endpoint>`
- **Auth:** query param `?token=SEU_TOKEN` (não é header).
- **Limite grátis:** ~15 requisições/dia, 8/hora. **Cacheie!** (1 req traz a
  série histórica inteira; guarde por 12–24h).
- **Resposta:** lista de objetos `[{ "d": "YYYY-MM-DD", "unixTs": "...", "<valor>": "1.23" }, ...]`.
  Pegue o último registro para o valor atual; ignore os campos de data/timestamp.
- Crie o token grátis em https://bitcoin-data.com/ (sem cartão).

### Endpoints usados (todos clássicos de ciclo de mercado)

| Indicador | Endpoint | Leitura (barato → caro) |
|-----------|----------|--------------------------|
| MVRV Ratio | `mvrv` | <1 barato; >3.5 topo |
| SOPR | `sopr` | <1 capitulação; >1 realização de lucro |
| MVRV Z-Score | `mvrv-zscore` | baixo = fundo; >6–7 = topo |
| NUPL | `nupl` | <0 medo/compra; >0.75 euforia |
| Puell Multiple | `puell-multiple` | baixo = fundo; alto = topo |
| Reserve Risk | `reserve-risk` | baixo = ótima relação risco/retorno |

> Outros endpoints disponíveis no mesmo token (não usados aqui, mas úteis):
> `fear-greed`, `puell-multiple`, `mvrv-zscore`, `nupl`, `reserve-risk`,
> `funding-rate`, `open-interest-1h`, `m2global`, `bgeometrics-index`.

Exemplo de chamada:
```
GET https://api.bgeometrics.com/v1/mvrv?token=SEU_TOKEN
-> [ {"d":"2025-01-01","unixTs":"...","mvrv":"1.95"}, ... ]
```

---

## 3b) On-chain GRÁTIS e SEM CHAVE — Coin Metrics Community

Melhor fonte on-chain sem cadastro: a Coin Metrics publica o dataset
"community" do BTC como CSV no GitHub.

- **URL:** `https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv`
- **Tamanho:** ~2,5 MB (série diária desde 2010). **Cacheie** por 12–24h.
- **Colunas usadas:** `time`, `PriceUSD`, `CapMrktCurUSD` (market cap),
  `CapMVRVCur` (MVRV), `IssTotUSD` (emissão do dia em USD).
- **Uso não comercial.** Crédito: Coin Metrics Community Data.

Dá para derivar as métricas de ciclo mais importantes:

```
realized_cap = CapMrktCurUSD / CapMVRVCur
MVRV         = CapMVRVCur
MVRV Z-Score = (market_cap − realized_cap) / desvio_padrão(market_cap)
               (use desvio EXPANDIDO, só com o passado, p/ não ter look-ahead)
NUPL         = 1 − 1/MVRV
Puell        = IssTotUSD / média_365d(IssTotUSD)
```

Bônus: o CSV também traz o **preço** (`PriceUSD`), então serve de fallback
quando Binance/CoinGecko estiverem bloqueados no seu servidor.

Não tem: SOPR, RHODL e Supply in Profit — para esses, só a BGeometrics
(seção 3) ou um proxy.

---

## 4) Termômetro — como vira um SCORE de −2 a +2

Cada indicador é convertido num score inteiro de **−2 (venda forte)** a
**+2 (compra forte)** por faixas. O **consolidado** é a média (simples ou
ponderada) dos indicadores escolhidos.

### Faixas (valor ≤ limiar → score)

Recalibradas contra a série real de 2010–2026 (as antigas liam o topo de
out/2025 como NEUTRO — ver seção 5 sobre a queda de amplitude dos ciclos).

```
mayer        (preço/MM200d):   ≤0.75:+2 ≤0.95:+1 ≤1.2:0  ≤1.5:-1  resto:-2
ma200w       (preço/MM200sem): ≤1.0:+2  ≤1.3:+1  ≤1.8:0  ≤2.2:-1  resto:-2
rsi_mensal   (RSI 14 mensal):  ≤45:+2   ≤57:+1   ≤67:0   ≤76:-1   resto:-2
             (a mediana do RSI mensal do BTC é ~63, não 50)
fng          (Fear&Greed):     ≤20:+2   ≤40:+1   ≤60:0   ≤80:-1   resto:-2
mvrv:                          ≤0.9:+2  ≤1.3:+1  ≤1.8:0  ≤2.3:-1  resto:-2
sopr:                          ≤0.95:+2 ≤1.0:+1  ≤1.02:0 ≤1.05:-1 resto:-2
mvrv_z       (mvrv-zscore):    ≤-0.2:+2 ≤0.8:+1  ≤2.0:0  ≤2.7:-1  resto:-2
nupl:                          ≤0.05:+2 ≤0.25:+1 ≤0.45:0 ≤0.60:-1 resto:-2
puell        (puell-multiple): ≤0.5:+2  ≤0.8:+1  ≤1.3:0  ≤1.9:-1  resto:-2
reserve_risk (reserve-risk):   ≤0.002:+2 ≤0.005:+1 ≤0.01:0 ≤0.02:-1 resto:-2
             (única sem fonte grátis; limiares clássicos, não conferidos)
```

MVRV, MVRV Z-Score, NUPL e Puell saem de graça da Coin Metrics (seção 3b) —
para o termômetro, só SOPR e Reserve Risk exigem a chave da BGeometrics.

### Score consolidado → rótulo

```
>= 1.5  COMPRA FORTE
>= 0.5  COMPRA
> -0.5  NEUTRO
> -1.5  VENDA
senão   VENDA FORTE
```

Indicadores calculados só do preço (sem chave):
- **Mayer Multiple** = preço / média móvel simples de 200 dias.
- **200W MA Ratio** = preço / média móvel de 200 semanas (~1400 dias).
- **RSI mensal** = RSI(14) sobre o preço reamostrado por mês.

---

## 5) Cycle Model — como vira um SCORE de 0 a 100 (ponto do ciclo)

Modelo diferente do termômetro: em vez de "compra/venda hoje", ele diz **onde
no ciclo** o mercado está e **qual posição carregar**. Escala contínua
(interpolação linear entre pontos, sem degraus), `0 = fundo`, `100 = euforia`.

### Escalas (valor → sub-score 0..100, interpolando)

Calibradas contra a série real de 2010–2026 (ver `scripts/07_calibracao.py`).
**Ponto-chave: a amplitude do ciclo cai a cada ciclo** — MVRV Z-Score nos
topos foi 8,9 (2013) → 8,9 (2017) → 5,3 (abr/21) → 3,5 (nov/21) → 2,9
(mar/24) → 2,5 (out/25). Escala de topo em "Z > 6" não dispara mais.

```
# --- on-chain real ---
mvrv_z:      -1→0  -0.5→4  0→10  0.5→20  1→30  1.5→40  2→52  2.5→72  3→82
             3.5→89  4.5→94  6→98  8→100
mvrv:        0.6→0  0.8→8  1→18  1.2→28  1.4→38  1.7→50  2→62  2.3→74
             2.7→84  3.2→91  4→96  5→100
nupl:      -0.5→0  -0.25→6  0→14  .15→25  .30→38  .42→50  .50→60  .56→70
             .62→79  .68→87  .75→95  .85→100
puell:       0.3→0  0.45→8  0.6→18  0.8→32  1→45  1.3→58  1.7→70  2.2→80
             3→89  5→96  9→100
supply_lucro (%): 50→0  60→10  70→22  78→34  85→48  90→60  94→72  97→85
             99→95  100→100
rhodl (log10):    2.6→0  3.0→14  3.3→28  3.6→44  3.9→60  4.1→74  4.35→88  4.7→100
sopr (MM7d):      0.95→0  0.98→16  1.00→35  1.01→50  1.02→65  1.035→80
             1.05→92  1.08→100

# --- proxies (só preço) — CADA UM COM ESCALA PRÓPRIA ---
z_extensao:  z-score de log(preço/MA200sem) em janela MÓVEL de 4 anos
             -2.3→0  -1.6→8  -1.2→18  -0.8→32  -0.29→50  0.2→62  0.6→72
             1.0→82  1.4→90  2.0→96  2.8→100
nupl_proxy (= 1 − MA200sem/preço):
             -0.5→0  -0.2→8  0→16  .15→27  .30→38  .44→50  .55→62  .65→73
             .75→84  .85→93  .95→100
supply_lucro_proxy (% dos últimos 1460 dias abaixo do preço de hoje):
             48→0  58→10  66→20  72→30  79→40  85→50  90→60  94→70  97→80
             99→90  100→100
drawdown (% do topo histórico):
             -85→0  -75→8  -65→18  -55→30  -46→42  -35→55  -25→66  -15→77
             -8→86  -3→94  0→100
mayer:       0.5→0  0.7→10  0.85→22  1→36  1.11→50  1.25→62  1.4→71  1.6→80
             1.9→89  2.4→96  3.5→100
rsi_mensal:  25→0  35→8  45→20  52→30  58→40  63→50  68→60  73→70  80→82
             88→93  95→100
             (a mediana do RSI mensal do BTC é ~63, NÃO 50 — usar 50→50
              faz o modelo ler fundo de ciclo como "neutro")
fng:         5→0  20→16  35→33  50→50  65→67  80→84  92→100
halving (dias): 0→32  180→46  350→62  520→85  560→90  700→62  900→38
             1100→18  1300→22  1460→30
```

Fora das pontas o valor "gruda" no extremo (0 ou 100).

### Pilares e pesos (média ponderada só dos que TÊM dado)

Ordem de preferência por pilar: BGeometrics (chave) → Coin Metrics (grátis) →
proxy de preço.

| Pilar | Peso | BGeometrics | Coin Metrics | Proxy (só preço) |
|-------|-----:|-------------|--------------|------------------|
| MVRV Z-Score | 0.22 | `mvrv-zscore` | MVRV Z-Score real | `z_extensao` |
| NUPL | 0.20 | `nupl` | NUPL real | `nupl_proxy` |
| Supply in Profit | 0.15 | `supply-in-profit` | — | `supply_lucro_proxy` |
| RHODL Ratio | 0.13 | `rhodl-ratio` / `reserve-risk` | Puell real | `drawdown` |
| SOPR | 0.10 | `sopr` (MM 7d) | — | `rsi_mensal` |
| Ciclo & Sentimento | 0.20 | — | — | média de Mayer + F&G + halving |

`score = Σ(sub_i × peso_i) / Σ(peso_i disponível)` — se um pilar falta, o peso
dele é redistribuído (o score fica sempre em 0..100).

Halvings de referência: `2012-11-28, 2016-07-09, 2020-05-11, 2024-04-20`.

### Fases e gestão de posição

```
score <15  FUNDO PROFUNDO   |  15-35 ACUMULAÇÃO  |  35-60 EXPANSÃO
60-80      DISTRIBUIÇÃO     |  >80   EUFORIA

exposição-alvo (%): 0→100  15→100  30→92  45→80  60→62  70→45  80→30  90→16  100→8
multiplicador DCA:  0→3.0  15→2.5  30→1.8  45→1.2  60→0.8  70→0.5  80→0.25  90→0
```

Para bot: mande **o score do fechamento semanal** (o modelo é de ciclo) e
avise quando a **fase mudar** — não a cada oscilação diária.

### Alerta de mudança de fase (com histerese)

Trocar de fase no limiar seco gera alerta todo dia quando o score oscila em
cima dele (34,9 / 35,1). Exija uma margem:

```
fase_nova = fase(score)
se fase_nova != fase_guardada:
    se subiu:   confirma só se score >= limiar_de_entrada + margem   (margem ~1.5)
    se desceu:  confirma só se score <= limiar_de_saída  − margem
    senão: mantém a fase guardada (não alerta)
guarde a fase confirmada e só avise quando ela mudar
```

Implementado em `cycle_model.fase_confirmada()` e usado pelo
`scripts/08_alerta.py`, que aceita `ALERTA_WEBHOOK` (a URL do `sendMessage`
do bot do Telegram funciona direto).

### Do score para a posição do usuário

```
alvo   = exposição-alvo(score)                  # curva da seção acima
atual  = valor_em_btc / patrimônio * 100
desvio = atual − alvo
se |desvio| <= banda (ex.: 5 p.p.): NÃO MEXER   # giro custa taxa e imposto
senão: ajuste_em_dinheiro = (alvo − atual)/100 * patrimônio
       (positivo = comprar, negativo = realizar — sempre em parcelas)
aporte_do_mês = aporte_base * multiplicador_DCA(score)
```

Implementado em `cycle_model.plano_rebalanceamento()`.

---

## 6) Pseudocódigo para o bot (mensagem diária)

```
1. preco = fetch_btc_price()            # cascata Binance/CC/CoinGecko
2. fng   = fetch_fear_greed()           # último valor
3. mayer, ma200w, rsi = calc do preço
4. onchain = {m: ultimo_valor(GET bgeometrics/<m>?token=..)  # CACHE 12-24h
              for m in [mvrv,sopr,mvrv-zscore,nupl,puell-multiple,reserve-risk]}
5. scores = [faixa(ind, valor) para cada indicador disponível]
6. consolidado = média(scores)
7. sinal = rótulo(consolidado)
8. Telegram: "BTC $<preço> | Sinal: <sinal> (score <x.xx>) | F&G <n>"
9. (opcional) ciclo = score 0-100 da seção 5 -> fase + exposição-alvo + DCA
   Telegram: "Ciclo: <fase> (<score>/100) | alvo <exp>% em BTC | DCA <mult>x"
```

Dicas para o bot:
- **Cacheie** os on-chain (1×/dia) para não estourar o limite de 15 req/dia.
- Trate TODA chamada com try/except → se falhar, ignore aquele indicador
  (NaN) e siga com os demais; nunca derrube o bot por uma fonte.
- Para alerta, dispare push quando o sinal cruzar para COMPRA FORTE / VENDA FORTE.

---

## 7) Aviso

Tudo aqui é **educativo**. Sinais e correlações **não preveem** o futuro e
**não são recomendação financeira ou de investimento**. Faça sua própria
pesquisa e gerencie risco.
