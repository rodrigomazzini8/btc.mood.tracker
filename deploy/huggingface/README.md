---
title: BTC Mood Tracker
emoji: 📈
colorFrom: yellow
colorTo: red
sdk: streamlit
app_file: dashboard.py
pinned: false
license: mit
---

# 📈 BTC Mood Tracker (Hugging Face Space)

Versão hospedada no **Hugging Face Spaces** — aqui o **FinBERT** funciona,
porque o Space tem RAM suficiente para o `torch` + `transformers`.

Cruza o preço do Bitcoin com o "humor" do mercado usando dados grátis
(Binance/CoinGecko, Fear & Greed, Google Trends) e IA de sentimento
(VADER e **FinBERT**, `ProsusAI/finbert`).

Ative a IA avançada no toggle **"Usar FinBERT"** na barra lateral. Na
primeira vez, o modelo (~440 MB) é baixado automaticamente.

> ⚠️ Conteúdo educativo. **Não é recomendação financeira.**
