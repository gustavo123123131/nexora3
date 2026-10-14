#!/usr/bin/env bash
# Executado automaticamente pelo GitHub Codespaces ao criar o ambiente remoto.
set -euo pipefail
cd "$(dirname "$0")/.."

# Tkinter (diálogos originais), display virtual e visualização remota do Chromium.
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3-tk xvfb x11vnc novnc websockify xauth

python -m pip install -r requirements-web.txt
# Instala Chromium e dependências Linux da versão do Playwright utilizada.
python -m playwright install --with-deps chromium
python -c 'import tkinter, fastapi, playwright, uvicorn; print("Dependências verificadas.")'
