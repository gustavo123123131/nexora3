#!/usr/bin/env bash
# Dependencias do Lixo CPA no GitHub Codespaces.
set -euo pipefail
cd "$(dirname "$0")/.."

# Algumas imagens de desenvolvimento incluem o repositorio Yarn com chave
# invalida (NO_PUBKEY 62D54FD4003F6525), mesmo sem o projeto usar Yarn.
# Mantenha a verificacao GPG habilitada para todos os repositorios.
update_apt() {
  local apt_log source changed=0
  apt_log="$(mktemp)"
  if sudo apt-get update -qq >"$apt_log" 2>&1; then
    rm -f "$apt_log"
    return 0
  fi

  cat "$apt_log" >&2
  if ! grep -q 'NO_PUBKEY 62D54FD4003F6525' "$apt_log" || \
     ! grep -q 'dl.yarnpkg.com/debian' "$apt_log"; then
    rm -f "$apt_log"
    echo 'APT falhou por outro motivo; nenhuma fonte foi alterada.' >&2
    return 1
  fi
  rm -f "$apt_log"
  echo 'Desabilitando somente a fonte Yarn com chave invalida...'

  for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [[ -f "$source" ]] || continue
    if grep -Eq '^[[:space:]]*deb[[:space:]].*dl\.yarnpkg\.com/debian' "$source"; then
      sudo cp -n "$source" "$source.lixocpa-backup" || true
      sudo sed -i -E '/^[[:space:]]*deb[[:space:]].*dl\.yarnpkg\.com\/debian/s/^/# Yarn desativado (NO_PUBKEY): /' "$source"
      changed=1
    fi
  done

  # Arquivos Deb822 dedicados ao Yarn tambem podem ser desabilitados.
  for source in /etc/apt/sources.list.d/*.sources; do
    [[ -f "$source" ]] || continue
    if grep -Eq '^[[:space:]]*URIs:.*dl\.yarnpkg\.com/debian' "$source"; then
      if [[ "$(grep -Ec '^[[:space:]]*URIs:' "$source")" == "1" ]] && \
         grep -Eq '^[[:space:]]*URIs:[[:space:]]*https?://dl\.yarnpkg\.com/debian/?[[:space:]]*$' "$source"; then
        sudo mv "$source" "$source.lixocpa-disabled"
        changed=1
      fi
    fi
  done

  if [[ "$changed" == "0" ]]; then
    echo 'Nao foi possivel isolar a fonte Yarn. Nenhuma outra fonte sera desabilitada.' >&2
    return 1
  fi
  sudo apt-get update -qq
}

update_apt
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3-tk xvfb x11vnc novnc websockify xauth

python -m pip install -r requirements-web.txt
python -m playwright install --with-deps chromium
python -c 'import tkinter, fastapi, playwright, uvicorn; print("Dependencias verificadas.")'
