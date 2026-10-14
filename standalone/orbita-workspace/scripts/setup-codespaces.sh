#!/usr/bin/env bash
# Dependências exclusivamente dentro do GitHub Codespaces.
set -euo pipefail
if [[ "${CODESPACES:-}" != "true" ]]; then
  echo "Este instalador só pode ser usado no GitHub Codespaces." >&2
  exit 1
fi
cd "$(dirname "$0")/.."
echo "[Órbita] Preparando desktop remoto dentro do Codespaces..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  xvfb x11vnc novnc websockify openbox xauth x11-utils xdotool \
  libgtk-3-0 libnss3 libxss1 libgbm1 libasound2 \
  libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxrandr2 \
  libxdamage1 libxcomposite1 libxfixes3 libx11-xcb1 \
  fonts-dejavu-core ca-certificates openssl
npm install --no-audit --no-fund
npm run build
echo "[Órbita] Preparação concluída no Codespaces."
