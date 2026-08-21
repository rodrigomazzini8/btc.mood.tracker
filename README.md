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
| On-chain (grátis, **sem chave**) | **Coin Metrics Community** | `raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv` | Market cap, realized cap e emissão desde 2010 → **MVRV, MVRV Z-Score, NUPL e Puell reais**. CSV de ~2,5 MB, cacheado 12h |

---

## 🌡️ Termômetro do Bitcoin (score consolidado)

Inspirado em dashboards de sinais, o dashboard tem uma seção **Termômetro**
que combina vários indicadores num único **score de −2 (venda forte) a +2
(compra forte)**. Cada indicador vira um score; o consolidado é a média dos
selecionados (com checkboxes para escolher quais entram).

- **Indicadores grátis, sem chave** (calculados do preço): Mayer Multiple,
  200W MA Ratio, RSI mensal, e o Fear & Greed.
- **On-chain grátis, sem chave**: MVRV, MVRV Z-Score, NUPL e Puell Multiple
  via **Coin Metrics** — aparecem sozinhos, sem configurar nada.
- **On-chain com chave (opcional)**: SOPR e Reserve Risk via **BGeometrics**
  (`api.bgeometrics.com`).

> As faixas de cada indicador foram **recalibradas contra a série real de
> 2010–2026**: as antigas eram de quando o BTC ia a MVRV 4+ e Mayer 3+, e por
> isso liam o topo de out/2025 como *NEUTRO*. Com as novas, o termômetro
> acerta o sinal em 11 de 11 viradas de ciclo (antes, 10). Confira com
> `python scripts/07_calibracao.py`.

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

Cada pilar usa a **primeira fonte disponível**, nesta ordem: BGeometrics
(com chave) → **Coin Metrics (grátis, sem chave)** → proxy calculado do preço.

| Pilar | Peso | Com `BGEO_API_KEY` | Grátis, sem chave (Coin Metrics) | Fallback (só preço) |
|-------|-----:|--------------------|----------------------------------|---------------------|
| MVRV Z-Score | 22% | MVRV Z-Score | **MVRV Z-Score real** | z-score de log(preço/MA200sem), janela de 4 anos |
| NUPL | 20% | NUPL | **NUPL real** | `1 − MA200sem/preço` |
| Supply in Profit | 15% | Supply in Profit | — | % dos dias dos últimos 4 anos abaixo do preço atual |
| RHODL Ratio | 13% | RHODL Ratio / Reserve Risk | **Puell Multiple real** | drawdown do topo histórico |
| SOPR | 10% | SOPR (média 7d) | — | RSI mensal |
| Ciclo & Sentimento | 20% | — | — | Mayer Multiple + Fear & Greed + relógio do halving |

> MVRV Z-Score e NUPL saem os dois da relação *market cap ÷ realized cap* —
> são pilares **correlacionados por construção** (42% do peso olhando a mesma
> coisa por dois ângulos). É proposital, mas vale saber.

Os proxies de MVRV/NUPL se apoiam num fato conhecido do mercado: a **média
móvel de 200 semanas anda historicamente colada no realized price** (o custo
médio da rede). É aproximação, não a métrica real — por isso o selo.

Se um pilar não tiver dado num dia, **o peso dele é redistribuído** entre os
demais: o score continua na mesma escala 0–100 e o rodapé do card mostra
quanto do peso total tinha dado (`% DO PESO`).

### Calibração contra a história real

Os limiares **não são chutados**: cada escala foi ajustada contra a série real
do BTC de 2010 a 2026 (Coin Metrics), olhando o que cada métrica marcou em
cada topo e fundo de ciclo. Rode você mesmo:

```bash
python scripts/07_calibracao.py            # com on-chain real
python scripts/07_calibracao.py --so-proxy # só com os proxies de preço
```

Leitura do modelo nas viradas de ciclo (com on-chain da Coin Metrics):

| Virada | Preço | Score | Fase |
|--------|------:|------:|------|
| Topo dez/2017 | $19.250 | 97 | EUFORIA |
| Fundo dez/2018 | $3.185 | 11 | FUNDO PROFUNDO |
| Crash covid mar/2020 | $5.628 | 16 | ACUMULAÇÃO |
| Topo abr/2021 | $62.869 | 90 | EUFORIA |
| Topo nov/2021 | $67.096 | 83 | EUFORIA |
| Fundo nov/2022 | $15.778 | 9 | FUNDO PROFUNDO |
| Topo mar/2024 | $71.505 | 79 | DISTRIBUIÇÃO |
| Topo out/2025 | $124.824 | 72 | DISTRIBUIÇÃO |
| Fundo fev/2026 | $63.495 | 21 | ACUMULAÇÃO |

11 de 11 viradas caem na fase certa (topo em distribuição/euforia, fundo em
fundo/acumulação), tanto com on-chain quanto só com os proxies.

**O que a calibração revelou** (e que a primeira versão errava feio):

1. **A amplitude do ciclo encolhe a cada ciclo.** MVRV Z-Score nos topos:
   8,9 (2013) → 8,9 (2017) → 5,3 (abr/21) → 3,5 (nov/21) → 2,9 (mar/24) →
   2,5 (out/25). Exigir "Z acima de 6" para chamar topo, como estava, nunca
   mais dispararia. O mesmo vale para NUPL (0,80 → 0,56) e Mayer (3,6 → 1,2).
2. **O proxy de MVRV estava quebrado.** O z-score com janela *expandida*
   morria a cada ciclo: marcava **−0,17 no topo de out/2025**, ou seja, dizia
   "fundo" numa máxima histórica. Trocado por z-score de `log(preço/MA200sem)`
   numa janela **móvel de 4 anos**, que se mantém comparável entre ciclos.
3. **RSI mensal do BTC tem mediana ~63, não 50.** A escala antiga (50 → score
   50) lia os fundos de 2018 (RSI 49) como "neutro". Recalibrada pela
   distribuição real.
4. **Cada proxy precisa de escala própria.** Usar a escala da métrica real no
   proxy dava leitura errada — as distribuições são diferentes.

Backtest da curva de exposição (mesmo script), sem taxas:

| Período | CAGR modelo | CAGR hold | Drawdown modelo | Drawdown hold |
|---------|------------:|----------:|----------------:|--------------:|
| desde 2011 | 43% | 125% | −80% | −93% |
| desde 2015 | 40% | 62% | −66% | −84% |
| desde 2018 | 25% | 23% | −66% | −81% |
| desde 2021 | 16% | 20% | −66% | −77% |

Honestamente: **no histórico completo o buy & hold ganha com folga** — a
tendência de 15 anos domina qualquer tentativa de posicionar por ciclo. O
modelo entrega **drawdown bem menor** e, do ciclo de 2018 pra cá, retorno
parecido. E ele continua comprando na queda inteira (é um modelo de valor,
não de momentum): em jun/2018 já estava 84% exposto com o BTC a $7,5k, antes
de cair para $3,2k.

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

### Quando o dado on-chain atrasa

Fonte de dado atrasa: fim de semana, manutenção, e às vezes semanas. O
comportamento ingênuo — carregar o último valor conhecido para a frente —
é o pior possível: o card mostraria um **MVRV de meses atrás ao lado do
preço de hoje**, sem avisar ninguém.

O modelo carrega um valor on-chain por no máximo **7 dias**. Passando disso:

1. o dado vira *ausente*, e o pilar **cai no proxy de preço**, que está sempre
   em dia (o peso continua valendo, só muda a fonte);
2. o card e o dashboard **dizem** que isso aconteceu, com o nome da fonte e o
   tamanho do atraso;
3. as linhas afetadas ganham o selo `PROXY` e a idade do dado em dias.

Ou seja: a leitura fica mais pobre, mas nunca falsa.

### Do score para a MINHA posição

Saber que o ciclo está em ACUMULAÇÃO não diz quanto comprar **hoje, com o que
você já tem**. A aba tem uma calculadora de rebalanceamento: você informa o
patrimônio considerado, quanto disso já está em BTC e o aporte recorrente
normal, e ela devolve o ajuste concreto até a exposição-alvo.

Duas decisões de projeto que evitam o erro clássico de rebalancear demais:

- **Banda de tolerância** (5 p.p. por padrão): dentro dela a resposta é
  *não mexer*. Giro custa taxa e imposto e quase não muda o risco.
- **Ajuste em parcelas**: o texto sempre manda parcelar, nunca virar a
  posição de uma vez.

Nada é salvo nem enviado — as contas acontecem na sua sessão.

### Alerta de mudança de fase

O modelo é de ciclo: olhar o score todo dia vira ansiedade. O que muda
decisão é a **virada de fase**.

```bash
python scripts/08_alerta.py            # fica calado se nada mudou
python scripts/08_alerta.py --forcar   # imprime a situação de qualquer jeito
```

Ele usa o score do **fechamento semanal**, exige uma **margem** para confirmar
a fase nova (senão um score oscilando em torno de 35 dispararia um alerta por
dia) e guarda a última fase em `cache/alerta_fase.json`. Serve para um cron
diário. Para receber em algum lugar:

```bash
export ALERTA_WEBHOOK="https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>"
python scripts/08_alerta.py
```

### Como usar

```bash
streamlit run dashboard.py         # aba "🔮 Cycle Model"
python scripts/06_cycle_model.py   # terminal + exporta cycle_model.html
python scripts/07_calibracao.py    # confere o modelo nas viradas históricas
python scripts/08_alerta.py        # avisa só quando o ciclo muda de fase
python scripts/cycle_model.py --autoteste   # testa o modelo offline
```

Sem chave nenhuma o modelo já roda com **MVRV, MVRV Z-Score, NUPL e Puell
reais** (Coin Metrics) e proxies só onde não há fonte grátis. Com a
`BGEO_API_KEY` (mesma chave grátis do termômetro, ver seção acima) entram
também SOPR, RHODL e Supply in Profit — e as métricas em comum
**compartilham o cache** do termômetro, sem gastar requisição duas vezes.

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
├── requirements-dev.txt    # pytest + flake8 (só para desenvolver)
├── setup.cfg               # configuração do lint e dos testes
├── .github/workflows/ci.yml # lint + testes em 3 versões de Python
├── tests/                  # suíte offline (não acessa a rede)
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
    ├── 07_calibracao.py        # confere os modelos nos topos/fundos reais
    ├── 08_alerta.py            # alerta (com histerese) de mudança de fase
    ├── coinmetrics.py          # on-chain grátis sem chave (Coin Metrics)
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
python scripts/07_calibracao.py       # 🎯 calibração do modelo x história real
python scripts/08_alerta.py           # 🔔 avisa quando o ciclo muda de fase
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

## 🧪 Testes e CI

O projeto tem uma suíte de testes **offline** (nenhum teste acessa a rede,
então roda rápido e não gasta cota de API nenhuma):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest          # 75 testes
flake8          # lint
python scripts/cycle_model.py --autoteste
```

O que a suíte protege:

- **integridade das escalas** dos dois modelos (ordem, monotonicidade, faixa
  0–100 / −2..+2) — o erro mais fácil de cometer ao recalibrar;
- **regressões de calibração**: há testes que falham se alguém voltar a exigir
  "MVRV-Z acima de 6" para marcar topo;
- **ausência de look-ahead**: cortar a série no meio não pode mudar o score
  dos dias anteriores ao corte;
- **degradação graciosa**: falha de rede em qualquer fonte devolve DataFrame
  vazio, nunca derruba o app;
- **matemática das métricas derivadas** (MVRV Z-Score, NUPL, Puell) contra
  fixtures conferidas na mão;
- **escapamento de HTML** no card (ele monta HTML na mão).

O CI (GitHub Actions, `.github/workflows/ci.yml`) roda lint + testes +
autoteste em **Python 3.9, 3.11 e 3.12** a cada push e pull request.

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
- [x] Faixas do Termômetro recalibradas e on-chain grátis (sem chave) nele também.
- [x] Suíte de testes offline + CI no GitHub Actions.
- [x] Rebalanceamento (com banda de tolerância) e alerta de mudança de fase.
- [ ] Mais fontes de humor (funding rate, dominância).
- [x] Modelo de ciclo 0–100 (Cycle Model) com card visual e plano de posição.
- [x] On-chain real **sem chave** (Coin Metrics) e escalas calibradas contra
      os topos e fundos reais de 2011–2026.
- [x] Exportar relatório HTML (card do Cycle Model, via script 06).
- [ ] Exportar relatório em PDF.
- [ ] Mais idiomas no sentimento (modelos multilíngues).

---

## ⚠️ Aviso

Este projeto é **educativo**. Nada aqui é **recomendação financeira ou de
investimento**. Mercados de cripto são voláteis e arriscados. Faça sua própria
pesquisa.
