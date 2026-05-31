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

## 🔌 Fontes de dados (todas grátis, sem chave)

| Sinal | Fonte | Endpoint | Observação |
|-------|-------|----------|-----------|
| Preço BTC | **Binance** | `/api/v3/klines` (`BTCUSDT`, diário) | Paginado com `startTime`/`endTime` para >1000 dias |
| Humor histórico | **Fear & Greed Index** (alternative.me) | `api.alternative.me/fng/?limit=0` | Escala 0–100, histórico desde 2018 |
| Atenção | **Google Trends** (via `pytrends`) | termo "Bitcoin" | *Opcional* — pode tomar rate limit (429) |
| Texto p/ IA | **Reddit** | `reddit.com/r/<sub>/new.json` | Precisa header `User-Agent`; só posts recentes |

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
    └── 05_finbert.py           # FinBERT lendo texto real do Reddit, x preço
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
```

### Dashboard interativo

```bash
streamlit run dashboard.py
```

O dashboard tem: **filtros de período**, **escolha de subreddits**, **toggle do
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

- [ ] Mais fontes de humor (funding rate, dominância, volume on-chain).
- [ ] Persistir o histórico do Reddit ao longo do tempo (banco local).
- [ ] Backtest simples de estratégias baseadas em humor (com aviso de risco).
- [ ] Exportar relatórios (PDF/HTML) a partir do dashboard.
- [ ] Mais idiomas no sentimento (modelos multilíngues).

---

## ⚠️ Aviso

Este projeto é **educativo**. Nada aqui é **recomendação financeira ou de
investimento**. Mercados de cripto são voláteis e arriscados. Faça sua própria
pesquisa.
