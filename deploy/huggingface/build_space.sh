#!/usr/bin/env bash
# ==========================================================================
# Monta uma pasta pronta para subir como Hugging Face Space.
#
# Uso (a partir da raiz do projeto):
#     bash deploy/huggingface/build_space.sh
#
# Resultado: cria a pasta ../btc-mood-space/ com:
#   dashboard.py, scripts/, README.md (cabeçalho HF) e requirements.txt (torch).
# Depois é só enviar essa pasta para o seu Space (ver GUIA.md).
# ==========================================================================
set -e

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${1:-$RAIZ/../btc-mood-space}"

echo ">> Montando Space em: $DEST"
mkdir -p "$DEST/scripts"

# App + módulos compartilhados (IA/FinBERT e Termômetro já estão no código).
cp "$RAIZ/dashboard.py"          "$DEST/dashboard.py"
cp "$RAIZ/scripts/common.py"     "$DEST/scripts/common.py"
cp "$RAIZ/scripts/termometro.py" "$DEST/scripts/termometro.py"

# Arquivos ESPECÍFICOS do Space (sobrescrevem os da raiz):
#  - README.md com o cabeçalho YAML que o HF exige
#  - requirements.txt COM torch/transformers (FinBERT)
cp "$RAIZ/deploy/huggingface/README.md"        "$DEST/README.md"
cp "$RAIZ/deploy/huggingface/requirements.txt" "$DEST/requirements.txt"

echo ">> Pronto. Conteúdo:"
ls -R "$DEST"
echo
echo ">> Agora siga deploy/huggingface/GUIA.md para enviar ao Space."
