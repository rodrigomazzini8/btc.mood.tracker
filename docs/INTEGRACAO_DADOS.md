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

## 4) Termômetro — como vira um SCORE de −2 a +2

Cada indicador é convertido num score inteiro de **−2 (venda forte)** a
**+2 (compra forte)** por faixas. O **consolidado** é a média (simples ou
ponderada) dos indicadores escolhidos.

### Faixas (valor ≤ limiar → score)

```
mayer        (preço/MM200d):   ≤0.8:+2  ≤1.0:+1  ≤1.5:0  ≤2.4:-1  resto:-2
ma200w       (preço/MM200sem): ≤1.0:+2  ≤1.5:+1  ≤3.0:0  ≤5.0:-1  resto:-2
rsi_mensal   (RSI 14 mensal):  ≤30:+2   ≤45:+1   ≤60:0   ≤70:-1   resto:-2
fng          (Fear&Greed):     ≤20:+2   ≤40:+1   ≤60:0   ≤80:-1   resto:-2
mvrv:                          ≤1.0:+2  ≤1.5:+1  ≤2.5:0  ≤3.5:-1  resto:-2
sopr:                          ≤0.95:+2 ≤1.0:+1  ≤1.02:0 ≤1.05:-1 resto:-2
mvrv_z       (mvrv-zscore):    ≤0.0:+2  ≤2.0:+1  ≤4.0:0  ≤6.0:-1  resto:-2
nupl:                          ≤0.0:+2  ≤0.25:+1 ≤0.5:0  ≤0.75:-1 resto:-2
puell        (puell-multiple): ≤0.5:+2  ≤1.0:+1  ≤2.0:0  ≤4.0:-1  resto:-2
reserve_risk (reserve-risk):   ≤0.002:+2 ≤0.005:+1 ≤0.01:0 ≤0.02:-1 resto:-2
```

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

```
mvrv_z:        -1→0   0→10  1→26  2→42  3→56  4→70  5→82  6.5→93  8→100
nupl:        -0.25→0   0→12  .25→32  .40→45  .50→58  .60→71  .70→86  .85→100
supply_lucro:   45→0  55→12  65→25  75→40  85→58  92→74  96→88  99→100   (em %)
rhodl (log10): 2.6→0  3.0→16  3.4→34  3.8→54  4.2→74  4.5→89  5.0→100
sopr (MM7d):  0.95→0  0.98→16  1.00→35  1.01→50  1.02→65  1.035→80  1.08→100
mayer:         0.6→0  0.8→14  1.0→30  1.3→46  1.7→62  2.2→79  3.5→100
fng:             5→0   20→16   35→33   50→50   65→67   80→84   92→100
halving (dias):  0→32  180→46  350→62  520→85  560→90  700→62  900→38  1100→18
```

Fora das pontas o valor "gruda" no extremo (0 ou 100).

### Pilares e pesos (média ponderada só dos que TÊM dado)

| Pilar | Peso | On-chain | Fallback grátis (só preço) |
|-------|-----:|----------|----------------------------|
| MVRV Z-Score | 0.22 | `mvrv-zscore` | z-score de `preço/MA200sem` |
| NUPL | 0.20 | `nupl` | `1 − MA200sem/preço` |
| Supply in Profit | 0.15 | `supply-in-profit` | % dos últimos 1460 dias com fechamento < preço de hoje |
| RHODL Ratio | 0.13 | `rhodl-ratio` (ou `reserve-risk`) | drawdown do topo histórico (%) |
| SOPR | 0.10 | `sopr` (MM 7d) | RSI mensal |
| Ciclo & Sentimento | 0.20 | — | média de Mayer + Fear&Greed + relógio do halving |

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
