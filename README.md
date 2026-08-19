# 📈 btc-mood-tracker

Cruza o **preço do Bitcoin** com o **"humor" do mercado** usando dados e IA —
**apenas com fontes gratuitas e sem nenhuma chave de API**.

A ideia: preço em cima (linha laranja), humor embaixo (verde quando acima da
média, vermelho quando abaixo). Calculamos a **correlação** entre os dois e
disponibilizamos tanto **scripts de linha de comando** quanto um **dashboard
interativo** em Streamlit.

> ⚠️ **Não é recomendação financeira.** Conteúdo educativo. Correlação não
> implica causalidade.

---

## 🎯 Objetivo

Coletar o preço do BTC e medidas de sentimento/atenção do mercado, calcular a
correlação (inclusive **defasada**, para investigar se o humor *antecipa* o
preço) e visualizar tudo de forma clara.

---

## 🔌 Fontes de dados (grátis; on-chain com chave grátis opcional)

| Sinal | Fonte | Endpoint | Observação |
|-------|-------|----------|-----------|
| Preço BTC | **Binance** | `/api/v3/klines` (`BTCUSDT`, diário) | Paginado com `startTime`/`endTime` para >1000 dias |
| Humor histórico | **Fear & Greed Index** (alternative.me) | `api.alternative.me/fng/?limit=0` | Escala 0–100, histórico desde 2018 |
| Atenção | **Google Trends** (via `pytrends`) | termo "Bitcoin" | *Opcional* — pode tomar rate limit (429) |
| Texto p/ IA | **Reddit** | `reddit.com/r/<sub>/new.json` | Precisa header `User-Agent`; só posts recentes |
| Texto p/ IA (fallback) | **CryptoCompare News** | `min-api.cryptocompare.com/data/v2/news/` | Usado quando o Reddit bloqueia datacenters (na nuvem); grátis, sem chave |
| On-chain (opcional) | **BGeometrics** (bitcoin-data.com) | `api.bgeometrics.com/v1/<metrica>?token=...` | MVRV, SOPR, MVRV Z-Score, NUPL, Puell, Reserve Risk. **Chave grátis** via `BGEO_API_KEY` (ver abaixo) |

---

## 🌡️ Termômetro do Bitcoin (score consolidado)

Inspirado em dashboards de sinais, o dashboard tem uma seção **Termômetro**
que combina vários indicadores num único **score de −2 (venda forte) a +2
(compra forte)**. Cada indicador vira um score; o consolidado é a média dos
selecionados (com checkboxes para escolher quais entram).

- **Indicadores grátis, sem chave** (calculados do preço): Mayer Multiple,
  200W MA Ratio, RSI mensal, e o Fear & Greed.
- **Indicadores on-chain (opcionais)**: MVRV, SOPR, MVRV Z-Score, NUPL,
  Puell Multiple, Reserve Risk — via **BGeometrics** (`api.bgeometrics.com`).
  Só aparecem se você definir a chave.

Recursos do termômetro: medidor (gauge) do score, tabela colorida por sinal,
contadores Compra/Neutro/Venda, expander explicando cada indicador, escolha
dos indicadores por checkbox, modo avançado com **pesos** por indicador e
gráfico do **score histórico × preço** (inclui on-chain). Os valores on-chain
são buscados 1×/dia e cacheados em memória (poupa a cota grátis da API).

### Como ativar os indicadores on-chain
1. Crie uma conta grátis em **https://bitcoin-data.com/** e gere sua API key
   (tier grátis; sem cartão).
2. Exponha a chave na variável de ambiente `BGEO_API_KEY`:
   ```bash
   export BGEO_API_KEY="sua_chave_aqui"      # Linux/Mac
   # setx BGEO_API_KEY "sua_chave_aqui"       # Windows
   streamlit run dashboard.py
   ```
   - **Streamlit Cloud:** App → *Settings* → *Secrets* → adicione
     `BGEO_API_KEY="..."`.
   - **Hugging Face Spaces:** Space → *Settings* → *Variables and secrets* →
     novo *Secret* `BGEO_API_KEY`.
3. Sem a chave, o termômetro funciona normalmente só com os indicadores grátis.

> ⚠️ Os limiares de cada indicador são didáticos/conservadores. **Não é
> recomendação financeira.** Correlação e sinais não preveem o futuro.

---

## 🔮 BTC Cycle Model (score de ciclo 0–100)

O **Cycle Model** responde a uma pergunta diferente do Termômetro. O termômetro
diz "compra ou venda hoje?"; o Cycle Model diz **em que ponto do ciclo de
mercado o Bitcoin está** — e traduz isso em **gestão de posição**.

- Score **contínuo de 0 a 100** (interpolação, não degraus):
  `0 = fundo profundo`, `100 = euforia`.
- Cinco fases: **FUNDO PROFUNDO** (<15) · **ACUMULAÇÃO** (15–35) ·
  **EXPANSÃO** (35–60) · **DISTRIBUIÇÃO** (60–80) · **EUFORIA** (>80).
- Card visual próprio (HTML+SVG, sem JS): medidor, composição do score,
  espectro das fases e o plano de ação da fase atual.

### Os pilares (e o que roda sem chave nenhuma)

Cada pilar tem a métrica on-chain "de verdade" e um **fallback grátis**
calculado só do preço — então o modelo **nunca fica mudo**, e a interface
marca com o selo `PROXY` toda linha que está no fallback.

| Pilar | Peso | On-chain (com `BGEO_API_KEY`) | Fallback grátis (só preço) |
|-------|-----:|-------------------------------|----------------------------|
| MVRV Z-Score | 22% | MVRV Z-Score | z-score da razão preço/MA200W |
| NUPL | 20% | NUPL | `1 − MA200W/preço` |
| Supply in Profit | 15% | Supply in Profit | % dos dias dos últimos 4 anos abaixo do preço atual |
| RHODL Ratio | 13% | RHODL Ratio (ou Reserve Risk) | drawdown do topo histórico |
| SOPR | 10% | SOPR (média 7d) | RSI mensal |
| Ciclo & Sentimento | 20% | — | Mayer Multiple + Fear & Greed + relógio do halving |

Os proxies de MVRV/NUPL se apoiam num fato conhecido do mercado: a **média
móvel de 200 semanas anda historicamente colada no realized price** (o custo
médio da rede). É aproximação, não a métrica real — por isso o selo.

Se um pilar não tiver dado num dia, **o peso dele é redistribuído** entre os
demais: o score continua na mesma escala 0–100 e o rodapé do card mostra
quanto do peso total tinha dado (`% DO PESO`).

### Do score para a posição

| Faixa | Fase | Ação | Exposição-alvo | DCA |
|-------|------|------|---------------:|----:|
| 0–15 | Fundo profundo | acumular agressivo | ~100% | 2,5–3,0× |
| 15–35 | Acumulação | acumular | 90–100% | 1,8–2,5× |
| 35–60 | Expansão | manter | 62–80% | 0,8–1,2× |
| 60–80 | Distribuição | realizar gradual | 30–62% | 0,25–0,8× |
| 80–100 | Euforia | realizar / caixa | 8–30% | 0× |

A exposição-alvo é uma **curva contínua** (nada de "tudo ou nada"), e a aba
mostra o **score semanal** — o modelo é de ciclo, então a decisão deve ser
tomada no fechamento da semana, não no ruído do dia. O **backtest** compara
seguir essa curva contra comprar e segurar (retorno, CAGR, drawdown, Sharpe).

### Como usar

```bash
streamlit run dashboard.py        # aba "🔮 Cycle Model"
python scripts/06_cycle_model.py  # terminal + exporta cycle_model.html
python scripts/cycle_model.py --autoteste   # testa o modelo offline
```

Sem chave, roda com os proxies. Com a `BGEO_API_KEY` (mesma chave grátis do
termômetro, ver seção acima), os pilares trocam automaticamente para as
métricas on-chain reais — e as métricas em comum **compartilham o cache** do
termômetro, sem gastar requisição duas vezes.

> ⚠️ Limiares de fundo e de topo **mudam a cada ciclo** (o mercado amadurece,
> a volatilidade cai). O modelo organiza a decisão; ele não prevê o futuro.
> **Não é recomendação financeira.**

---

## 🧠 A "IA" (análise de sentimento)

- **VADER** (`vaderSentiment`) — leve, baseado em regras. Versão didática.
- **FinBERT** (`ProsusAI/finbert` via `transformers`) — Transformer ajustado a
  texto financeiro. Classifica positivo/neutro/negativo e converte em nota
  ponderada pela confiança:
  `nota = P(pos)·(+1) + P(neutro)·0 + P(neg)·(−1)`, resultando em algo entre −1 e +1.

---

## 📁 Estrutura de pastas

```
btc-mood-tracker/
├── dashboard.py            # app Streamlit unindo tudo (Plotly, filtros, cache)
├── requirements.txt
├── README.md
├── .gitignore              # ignora cache/, *.csv, *.png, __pycache__, modelos HF
└── scripts/
    ├── common.py               # funções compartilhadas (fontes, análise, gráfico)
    ├── 01_simples_vader.py     # BTC + Reddit + VADER + gráfico
    ├── 02_indices_gratis.py    # BTC + Fear & Greed; correlação; gráfico
    ├── 03_duas_fontes.py       # + Google Trends como 2ª linha de humor
    ├── 04_cache_defasagem.py   # cache CSV, média móvel, correlação defasada
    ├── 05_finbert.py           # FinBERT lendo texto real do Reddit, x preço
    ├── 06_cycle_model.py       # BTC Cycle Model no terminal + card HTML
    ├── termometro.py           # score consolidado -2..+2 (indicadores soltos)
    └── cycle_model.py          # modelo de CICLO 0-100 + card visual + backtest
```

---

## 🛠️ Instalação

Requer Python 3.9+.

```bash
# (opcional, recomendado) ambiente virtual
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

> O `requirements.txt` é **leve** (núcleo + VADER), pronto para deploy grátis.
> O **FinBERT** (`torch` + `transformers`) é pesado e fica num arquivo
> separado. Para usar a IA avançada (script 05 e toggle do dashboard)
> **localmente**, instale também:
>
> ```bash
> pip install -r requirements-finbert.txt
> ```
>
> Sem isso, o dashboard cai automaticamente para o VADER.

---

## ▶️ Como rodar

### Scripts (linha de comando)

Cada script roda sozinho, **imprime a correlação** no terminal e **salva um PNG**:

```bash
python scripts/02_indices_gratis.py   # ⭐ comece por aqui (mais simples, sem IA pesada)
python scripts/03_duas_fontes.py      # + Google Trends
python scripts/04_cache_defasagem.py  # cache CSV + média móvel + correlação defasada
python scripts/01_simples_vader.py    # Reddit + VADER
python scripts/05_finbert.py          # Reddit + FinBERT (baixa o modelo na 1ª vez)
python scripts/06_cycle_model.py      # 🔮 score de ciclo 0-100 + cycle_model.html
```

### Dashboard interativo

```bash
streamlit run dashboard.py
```

O dashboard tem 5 abas (**🔮 Cycle Model**, Termômetro, Preço & Humor,
Backtest e IA), **filtros de período**, **escolha de subreddits**, **toggle do
FinBERT**, **métricas no topo** (preço, Fear & Greed, correlação), **gráfico
Plotly interativo** e uma **tabela dos posts classificados pela IA**. Ele
renderiza mesmo que o Google Trends ou o FinBERT estejam indisponíveis.

---

## ☁️ Deploy grátis (Streamlit Community Cloud)

O dashboard pode ir ao ar de graça, sem servidor próprio:

1. Garanta que o código está no GitHub (este repositório já está).
2. Acesse **https://share.streamlit.io** e entre com sua conta do GitHub.
3. Clique em **"New app"** e preencha:
   - **Repository:** `rodrigomazzini8/btc.mood.tracker`
   - **Branch:** `main` (ou a branch do projeto)
   - **Main file path:** `dashboard.py`
4. Clique em **Deploy**. Ele instala o `requirements.txt` (leve) e sobe o app
   numa URL pública tipo `https://<seu-app>.streamlit.app`.

Observações:
- O `requirements.txt` é propositalmente **leve** (sem `torch`) para caber no
  tier grátis. No deploy, o toggle do FinBERT cai para o **VADER**.
- Quer o **FinBERT no ar**? Use o **Hugging Face Spaces** (mais RAM): crie um
  Space tipo *Streamlit*, suba os arquivos e adicione `torch`/`transformers`
  ao `requirements.txt` do Space.
- ⚠️ **Vercel/Netlify não servem** para Streamlit (são para sites estáticos /
  funções serverless de curta duração, não um servidor WebSocket de longa
  duração).

---

## ✅ O que esperar

- **Correlação positiva** entre preço e Fear & Greed costuma aparecer: quando o
  mercado está "ganancioso", o preço tende a estar mais alto. Mas isso **varia**
  por período e **não prevê** o futuro.
- O **Reddit** só entrega posts recentes, então os scripts de sentimento (01 e
  05) cobrem poucos dias — são demonstrações do método.

---

## 🗺️ Roadmap

- [x] Indicadores on-chain no Termômetro (MVRV, SOPR, NUPL, Puell, etc.).
- [x] Backtest da estratégia do score (Sharpe, win rate, drawdown, nº de ops).
- [x] Header de destaque com preço, variação 24h e sinal do termômetro.
- [x] Histórico próprio: log diário do score (1 linha/dia) com gráfico.
- [x] Alertas visuais de zona (COMPRA FORTE / VENDA FORTE).
- [ ] Mais fontes de humor (funding rate, dominância).
- [x] Modelo de ciclo 0–100 (Cycle Model) com card visual e plano de posição.
- [x] Exportar relatório HTML (card do Cycle Model, via script 06).
- [ ] Exportar relatório em PDF.
- [ ] Mais idiomas no sentimento (modelos multilíngues).

---

## ⚠️ Aviso

Este projeto é **educativo**. Nada aqui é **recomendação financeira ou de
investimento**. Mercados de cripto são voláteis e arriscados. Faça sua própria
pesquisa.
