# 🚀 Guia: rodar o FinBERT no Hugging Face Spaces (grátis)

O FinBERT (`torch` + `transformers` + modelo ~440 MB) é pesado demais para o
Streamlit Community Cloud grátis (~1 GB de RAM). O **Hugging Face Spaces** tem
mais RAM e roda o FinBERT sem problema. O código do projeto **já suporta**
FinBERT — só falta hospedá-lo num ambiente com as dependências pesadas.

Há dois caminhos. O **A** é o mais fácil (interface web).

---

## Caminho A — pela interface web (recomendado)

### 1. Crie o Space
1. Acesse **https://huggingface.co/spaces** e clique em **"Create new Space"**.
   (Crie uma conta grátis se ainda não tiver.)
2. Preencha:
   - **Owner:** seu usuário
   - **Space name:** `btc-mood-tracker`
   - **License:** MIT
   - **SDK:** **Streamlit**
   - **Hardware:** *CPU basic* (grátis) já roda o FinBERT (só é mais lento na
     1ª vez, ao baixar o modelo).
3. Clique em **Create Space**.

### 2. Envie os arquivos
No seu Space, abra a aba **"Files"** → **"Add file"** → **"Upload files"** e
envie **estes arquivos** (estrutura idêntica):

```
dashboard.py                      (copie da raiz do projeto)
scripts/common.py                 (copie de scripts/)
README.md                         (use deploy/huggingface/README.md)
requirements.txt                  (use deploy/huggingface/requirements.txt — COM torch)
```

> ⚠️ Importante: use o **README.md** e o **requirements.txt** desta pasta
> (`deploy/huggingface/`), NÃO os da raiz. O README do Space tem um cabeçalho
> especial e o requirements inclui o `torch`/`transformers`.

Dica: rode `bash deploy/huggingface/build_space.sh` para gerar automaticamente
uma pasta `../btc-mood-space/` já com tudo organizado — aí é só arrastar.

### 3. Aguarde o build
O Space instala as dependências e sobe sozinho (alguns minutos na 1ª vez,
porque o `torch` é grande). Quando ficar verde ("Running"), abra o app.

### 4. Ligue o FinBERT
Na barra lateral, ative o toggle **"Usar FinBERT (IA avançada)"**. Na primeira
classificação o modelo `ProsusAI/finbert` (~440 MB) é baixado — depois fica em
cache. A tabela passa a mostrar o modelo **FinBERT**.

---

## Caminho B — por git (linha de comando)

```bash
# 1) Monte a pasta do Space
bash deploy/huggingface/build_space.sh

# 2) Crie o Space no site (passo 1 do Caminho A) e copie a URL git dele, algo como:
#    https://huggingface.co/spaces/SEU_USUARIO/btc-mood-tracker

cd ../btc-mood-space
git init
git remote add origin https://huggingface.co/spaces/SEU_USUARIO/btc-mood-tracker
git add .
git commit -m "deploy: btc-mood-tracker com FinBERT"
git push -u origin main      # pode pedir login/token do Hugging Face
```

> O token do HF é criado em **Settings → Access Tokens** no site do Hugging
> Face (use um token com permissão de *write*).

---

## Dúvidas comuns

- **"Demora na primeira classificação"** — normal: é o download do modelo
  (~440 MB). Depois fica rápido (cache via `@st.cache_resource`).
- **"Quero a IA avançada também localmente"** — na raiz do projeto:
  `pip install -r requirements-finbert.txt` e rode `streamlit run dashboard.py`.
- **Preço/Reddit na nuvem** — o app já tem fallback (CoinGecko para preço,
  CryptoCompare para texto), então funciona em qualquer host.

> ⚠️ Conteúdo educativo. **Não é recomendação financeira.**
