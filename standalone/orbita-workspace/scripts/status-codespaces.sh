#!/usr/bin/env bash
set -euo pipefail
if [[ "${CODESPACES:-}" != "true" ]]; then
  echo "Disponível somente no GitHub Codespaces." >&2
  exit 1
fi
DIR="$HOME/.local/share/orbita-codespaces"
echo "Órbita Workspace · acesso remoto privado"
for name in display windows vnc novnc electron; do
  if [[ -s "$DIR/$name.pid" ]] && kill -0 "$(cat "$DIR/$name.pid")" 2>/dev/null; then
    echo "  $name: em execução"
  else
    echo "  $name: indisponível"
  fi
done
if [[ -r "$DIR/vnc.password" ]]; then
  echo "Senha VNC da tela remota: $(cat "$DIR/vnc.password")"
else
  echo "Senha não gerada; execute o script de inicialização."
fi
echo "Abra PORTS → 6081 (PRIVATE) → /vnc.html?autoconnect=true"
echo "Logs do aplicativo: tail -n 60 $DIR/electron.log"
