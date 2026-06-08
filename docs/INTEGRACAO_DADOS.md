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

## 5) Pseudocódigo para o bot (mensagem diária)

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
```

Dicas para o bot:
- **Cacheie** os on-chain (1×/dia) para não estourar o limite de 15 req/dia.
- Trate TODA chamada com try/except → se falhar, ignore aquele indicador
  (NaN) e siga com os demais; nunca derrube o bot por uma fonte.
- Para alerta, dispare push quando o sinal cruzar para COMPRA FORTE / VENDA FORTE.

---

## 6) Aviso

Tudo aqui é **educativo**. Sinais e correlações **não preveem** o futuro e
**não são recomendação financeira ou de investimento**. Faça sua própria
pesquisa e gerencie risco.
