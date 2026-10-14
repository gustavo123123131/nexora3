#!/usr/bin/env bash
# Interface Electron e perfis na tela virtual; VNC por Codespaces Private.
set -euo pipefail
if [[ "${CODESPACES:-}" != "true" ]]; then
  echo "Use este comando apenas dentro do GitHub Codespaces." >&2
  exit 1
fi
cd "$(dirname "$0")/.."
STATE_DIR="$HOME/.local/share/orbita-codespaces"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
exec 9>"$STATE_DIR/start.lock"
flock -w 90 9
export DISPLAY=:98

running() {
  local pid
  [[ -s "$STATE_DIR/$1.pid" ]] || return 1
  pid="$(cat "$STATE_DIR/$1.pid")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}
spawn() {
  local name="$1"
  shift
  if running "$name"; then return; fi
  # O filho não herda o lock do iniciador; o comando pode ser repetido com segurança.
  nohup "$@" >>"$STATE_DIR/$name.log" 2>&1 </dev/null 9>&- &
  echo "$!" > "$STATE_DIR/$name.pid"
}

# A cada inicialização, conferir a versão do branch sem mudar os dados.
npm install --no-audit --no-fund >"$STATE_DIR/install.log" 2>&1
# npm pode substituir o auxiliar do Chromium ao atualizar dependências.
# Restaurar suas permissões seguras em todo início de sessão.
if [[ -f node_modules/electron/dist/chrome-sandbox ]]; then
  sudo chown root:root node_modules/electron/dist/chrome-sandbox
  sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
fi
npm run build >"$STATE_DIR/build.log" 2>&1 || {
  echo "[Órbita] Build falhou: veja $STATE_DIR/build.log" >&2
  exit 1
}

spawn display Xvfb :98 -screen 0 1440x900x24 -nolisten tcp
for _ in $(seq 1 50); do
  [[ -S /tmp/.X11-unix/X98 ]] && break
  sleep .2
done
[[ -S /tmp/.X11-unix/X98 ]] || { echo "Falha ao abrir display virtual." >&2; exit 1; }
spawn windows openbox --display :98

PASSWORD_FILE="$STATE_DIR/vnc.password"
VNC_AUTH="$STATE_DIR/vnc.auth"
if [[ ! -s "$VNC_AUTH" ]]; then
  openssl rand -hex 4 > "$PASSWORD_FILE"
  chmod 600 "$PASSWORD_FILE"
  x11vnc -storepasswd "$(cat "$PASSWORD_FILE")" "$VNC_AUTH" >/dev/null 2>&1
  chmod 600 "$VNC_AUTH"
fi
spawn vnc x11vnc -display :98 -localhost -forever -shared \
  -rfbauth "$VNC_AUTH" -rfbport 5901 -noxdamage
spawn novnc websockify --web=/usr/share/novnc 0.0.0.0:6081 127.0.0.1:5901

# Chromium com sandbox habilitado; não relaxar proteção para acessar sites externos.
spawn electron ./node_modules/.bin/electron . --disable-gpu --disable-dev-shm-usage

for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:6081/vnc.html >/dev/null 2>&1; then break; fi
  sleep .2
done
if ! curl -fsS --max-time 2 http://127.0.0.1:6081/vnc.html >/dev/null; then
  echo "[Órbita] noVNC indisponível; veja $STATE_DIR/novnc.log" >&2
  exit 1
fi
for _ in $(seq 1 60); do
  if xdotool search --name 'Órbita Workspace' >/dev/null 2>&1; then break; fi
  sleep .2
done
if ! running electron || ! xdotool search --name 'Órbita Workspace' >/dev/null 2>&1; then
  echo "[Órbita] A janela Electron não apareceu; veja $STATE_DIR/electron.log" >&2
  exit 1
fi
cat <<'EOF'
ÓRBITA WORKSPACE — SEM INSTALAR NADA NO COMPUTADOR
1. Abra PORTS no Codespaces e mantenha a porta 6081 como PRIVATE.
2. Abra a URL da porta 6081 e entre em /vnc.html?autoconnect=true.
3. Use 'bash standalone/orbita-workspace/scripts/status-codespaces.sh'
   no terminal para obter a senha da tela remota.
As janelas dos perfis abrirão no Codespaces, não no seu Windows.
EOF
