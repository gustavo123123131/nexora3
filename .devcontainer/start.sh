#!/usr/bin/env bash
# Inicia automaticamente o aplicativo apenas no Codespaces, sem baixar nada.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${CODESPACES:-}" != "true" ]]; then
  echo 'O modo remoto só deve ser iniciado no GitHub Codespaces.' >&2
  exit 1
fi

export DISPLAY=:99
if ! pgrep -f 'Xvfb :99 ' >/dev/null 2>&1; then
  nohup Xvfb :99 -screen 0 1600x900x24 -nolisten tcp > /tmp/lixocpa-xvfb.log 2>&1 < /dev/null &
fi
# O servidor X pode levar alguns instantes para criar o socket.
for _ in $(seq 1 30); do
  [[ -S /tmp/.X11-unix/X99 ]] && break
  sleep 0.2
done

# VNC permanece atrás do proxy PRIVADO do Codespaces; não configure portas públicas.
if ! pgrep -f 'x11vnc -display :99 ' >/dev/null 2>&1; then
  nohup x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 \
    > /tmp/lixocpa-vnc.log 2>&1 < /dev/null &
fi
if ! pgrep -f 'websockify --web=/usr/share/novnc ' >/dev/null 2>&1; then
  nohup websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 \
    > /tmp/lixocpa-novnc.log 2>&1 < /dev/null &
fi

# O reloader continua ativo: alterações JS/CSS/Python atualizam o site.
if ! curl -fsS --max-time 2 http://127.0.0.1:8765/ >/dev/null 2>&1; then
  nohup python servidor.py > /tmp/lixocpa-web.log 2>&1 < /dev/null &
fi

echo 'Lixo CPA: abra a porta privada 8765 (Painel Web) em PORTS.'
echo 'Janelas Chromium: porta privada 6080, /vnc.html?autoconnect=true (opcional).'
