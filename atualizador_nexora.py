"""Supervisor for Nexora's Windows agent: update without redownloading a CMD.

One installer run sets this up; afterward the same console supervises the
child agent, checks a PINNED GitHub branch and reloads it when browsers are idle.
No credentials or Chromium profile folders are ever sent to GitHub.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote
import zipfile

REPO = "gustavo123123131/nexora3"
BRANCH = "fix/remover-criacao-contas"
INTERVAL_SECONDS = 180  # 20 checks/hour, below GitHub's anonymous 60/hour limit
MAX_ZIP_BYTES = 90 * 1024 * 1024
MAX_EXTRACT_BYTES = 300 * 1024 * 1024
MAX_FILES = 7000
STATE_NAME = "nexora-update-state.json"
CONFIG_NAME = "nexora-updater-config.json"
MANIFEST_NAME = "nexora-managed-source.json"
AGENT_PORT = 8767
OBSOLETE_CODE = (
    "nexora_accounts/profile_data.py",
    "nexora_accounts/registration_autofill.py",
    "nexora_accounts/registration_submit.py",
    "nexora_accounts/registration_trace.py",
    "nexora_accounts/service.py",
    "Settings/Criar_Mae/settings/actions.py",
    "Settings/Criar_Mae/settings/identity.py",
    "Settings/Criar_Mae/settings/nomes.txt",
    "Settings/Criar_Filha/settings/actions.py",
    "Settings/Criar_Filha/settings/identity.py",
    "Settings/Criar_Filha/settings/nomes.txt",
    "docs/DIAGNOSTICO_CONTAS.md",
    "docs/REGISTRATION_PREFILL.md",
)
SOURCE_SUFFIXES = {
    ".py", ".js", ".css", ".html", ".txt", ".md",
    ".ps1", ".cmd", ".bat", ".json", ".toml", ".ini",
    ".cfg", ".yaml", ".yml", ".svg", ".png", ".ico",
    ".webp", ".woff", ".woff2", ".sh",
}
PRIVATE_PARTS = {
    ".venv", ".git", "logs", "__profiles", "__pycache__", "contas",
    ".runtime", ".pytest_cache", "node_modules", ".nexora-update",
}
PRIVATE_FILENAMES = {
    "accounts.json", "registros.json", "proxies.json",
    "settings.json", "versao-instalada.txt",
    STATE_NAME, CONFIG_NAME, MANIFEST_NAME,
}
REQUIRED = (
    "servidor.py", "agente_windows.py", "atualizador_nexora.py",
    "Settings/Criar_Mae/gui_tab.py", "Settings/Criar_Filha/gui_tab.py",
    "webui/static/app.js",
    "webui/static/accounts_view.js",
    "webui/static/accounts_view.css",
    "webui/static/backup_view.js",
    "Settings/registration_autofill.py",
)


def _request(url: str, *, timeout: int = 12):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Nexora-Windows-AutoUpdater/1.0",
                 "Accept": "application/vnd.github+json"},
    )
    # Keep a corporate/user proxy for external GitHub access.
    return urllib.request.urlopen(req, timeout=timeout)


def newest_commit(branch: str = BRANCH) -> str:
    if branch != BRANCH:
        raise ValueError("Atualização automática disponível somente na branch autorizada.")
    url = (f"https://api.github.com/repos/{REPO}/git/ref/heads/"
           + quote(branch, safe="/"))
    with _request(url) as response:
        data = json.load(response)
    sha = data.get("object", {}).get("sha", "")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise ValueError("GitHub retornou identificador de versão inválido.")
    return sha


def _allowed(path: str) -> bool:
    if "\\" in path or "\x00" in path or path.startswith("/"):
        return False
    p = PurePosixPath(path)
    if not p.parts or any(x in ("", ".", "..") for x in p.parts):
        return False
    if any(part.lower() in PRIVATE_PARTS for part in p.parts):
        return False
    if p.name.lower() in PRIVATE_FILENAMES:
        return False
    return (p.suffix.lower() in SOURCE_SUFFIXES or
            p.name in ("LICENSE", "NOTICE"))


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return default


def _write_json(path: Path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def _safe_path(root: Path, relative: str) -> Path:
    if not _allowed(relative):
        raise ValueError("Arquivo de atualização fora dos diretórios permitidos.")
    result = (root / Path(*PurePosixPath(relative).parts)).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Arquivo fora da pasta do Nexora.")
    return result


def collect_sources(archive_bytes: bytes) -> dict[str, bytes]:
    """Validate ALL ZIP entries before writing to the installed app."""
    result: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
        infos = [x for x in z.infolist() if not x.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError("Arquivo de atualização com excesso de entradas.")
        for item in infos:
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Links simbólicos não são permitidos no pacote.")
            if item.file_size > 25 * 1024 * 1024:
                raise ValueError("Arquivo individual excede o limite.")
            total += item.file_size
            if total > MAX_EXTRACT_BYTES:
                raise ValueError("Atualização muito grande.")
            parts = item.filename.split("/", 1)
            if len(parts) != 2:
                raise ValueError("ZIP de código sem diretório-raiz esperado.")
            relative = parts[1]
            if not _allowed(relative):
                continue
            if relative in result:
                raise ValueError("Arquivo duplicado na atualização.")
            result[relative] = z.read(item)
    for name in REQUIRED:
        if name not in result:
            raise ValueError(f"Atualização incompleta: faltando {name}.")
    server = result["servidor.py"]
    if b"NEXORA_EDITION = 'browser-only'" not in server:
        raise ValueError("Pacote não é a versão Nexora sem cadastro.")
    for name in ("Settings/Criar_Mae/gui_tab.py",
                 "Settings/Criar_Filha/gui_tab.py"):
        if (b"Abrir Navegadores" not in result[name] or
                b"create_launch_profiles" in result[name]):
            raise ValueError("Pacote contém gerador de contas antigo.")
    if "nexora_accounts/registration_autofill.py" in result:
        raise ValueError("Pacote contém cadastro automático removido.")
    return result


def fetch_release(sha: str) -> dict[str, bytes]:
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise ValueError("SHA inválido.")
    url = f"https://github.com/{REPO}/archive/{sha}.zip"
    with _request(url, timeout=35) as response:
        archive = response.read(MAX_ZIP_BYTES + 1)
    if len(archive) > MAX_ZIP_BYTES:
        raise ValueError("ZIP excede o limite de tamanho.")
    return collect_sources(archive)


def install_release(root: Path, files: dict[str, bytes], sha: str,
                    *, fail_after: int = -1) -> None:
    """Replace app code with rollback. Never modify sessions/settings/proxies.

    Must be called ONLY after the managed Python agent has stopped and with
    no active Chromium sessions.
    """
    root = root.resolve()
    known = _load_json(root / MANIFEST_NAME, [])
    if not isinstance(known, list):
        known = []
    previous = {x for x in known if isinstance(x, str) and _allowed(x)}
    current = set(files)
    old = {x for x in OBSOLETE_CODE if _allowed(x)}
    # The old bridge is closed-source-derived registration experiment, not a
    # user profile; it is safe to remove only these known source extensions.
    bridge = root / "proxybet_registration_bridge"
    if bridge.is_dir():
        for child in bridge.rglob("*"):
            if child.is_file():
                rel = child.relative_to(root).as_posix()
                if _allowed(rel):
                    old.add(rel)

    targets = sorted(current | (previous - current) | old)
    backups: dict[str, bytes | None] = {}
    state_backup = (root / STATE_NAME).read_bytes() if (root / STATE_NAME).exists() else None
    manifest_backup = (root / MANIFEST_NAME).read_bytes() if (root / MANIFEST_NAME).exists() else None
    for rel in targets:
        target = _safe_path(root, rel)
        backups[rel] = target.read_bytes() if target.exists() else None

    with tempfile.TemporaryDirectory(prefix="nexora-update-") as stage_dir:
        stage = Path(stage_dir)
        try:
            for index, rel in enumerate(targets):
                target = _safe_path(root, rel)
                if rel in current:
                    temp = _safe_path(stage, rel)
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    temp.write_bytes(files[rel])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(temp, target)
                elif target.is_file():
                    target.unlink()
                if index == fail_after:
                    raise OSError("Falha de escrita simulada")
            _write_json(root / MANIFEST_NAME, sorted(current))
            _write_json(root / STATE_NAME, {"branch": BRANCH, "sha": sha})
        except Exception:
            for rel in targets:
                original = backups[rel]
                target = _safe_path(root, rel)
                if original is None:
                    if target.is_file():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    restore = _safe_path(stage, rel)
                    restore.parent.mkdir(parents=True, exist_ok=True)
                    restore.write_bytes(original)
                    os.replace(restore, target)
            for name, original in ((STATE_NAME, state_backup),
                                   (MANIFEST_NAME, manifest_backup)):
                target = root / name
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.write_bytes(original)
            raise


def idle_browser_state() -> bool:
    # Force no proxy for loopback and do not send the private pairing token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(
        f"http://127.0.0.1:{AGENT_PORT}/api/update/idle", timeout=3
    ) as response:
        data = json.load(response)
    return data.get("edition") == "browser-only" and data.get("idle") is True


def terminate_agent(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@contextmanager
def single_instance(root: Path):
    """Prevent two updater consoles from writing into the same installation."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".nexora-supervisor.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "O Nexora ja esta aberto em outra janela. Use o agente existente."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_unlocked(root: Path, *, branch: str = BRANCH, panel: str | None = None,
        interval: int = INTERVAL_SECONDS):
    from agente_windows import validate_panel_url

    if branch != BRANCH:
        raise ValueError("O supervisor só atualiza a branch fix/remover-criacao-contas.")
    root = root.resolve()
    conf = root / CONFIG_NAME
    saved = _load_json(conf, {})
    if not isinstance(saved, dict):
        saved = {}
    panel = panel or saved.get("panel") or input(
        "Cole o link HTTPS da porta 8765 do Codespaces: "
    ).strip()
    panel = validate_panel_url(panel)
    _write_json(conf, {"panel": panel, "branch": BRANCH})
    # Reuse the pairing key while the supervisor itself is running. A code
    # update does not require reconnecting manually in the browser.
    key = secrets.token_urlsafe(24)
    env = os.environ.copy()
    env["LIXOCPA_AGENT_KEY"] = key
    child: subprocess.Popen | None = None
    failures = 0
    last_check = 0.0
    child_started = 0.0
    pending_sha = None
    pending_files = None

    print("\nNEXORA: atualizações automáticas ligadas (a cada 3 minutos).", flush=True)
    print("O mesmo iniciador serve para todas as próximas versões.", flush=True)
    print("Com navegador aberto, a atualização aguarda o fechamento.", flush=True)

    try:
        while True:
            if child is None or child.poll() is not None:
                if child is not None:
                    if time.monotonic() - child_started > 90:
                        failures = 0
                    failures += 1
                    if failures >= 4:
                        raise RuntimeError(
                            "Agente encerrou repetidamente. Verifique o erro acima."
                        )
                    time.sleep(3)
                child = subprocess.Popen(
                    [sys.executable, "agente_windows.py", "--painel", panel],
                    cwd=root, env=env,
                )
                child_started = time.monotonic()
                print("NEXORA: processo Windows iniciado.", flush=True)

            now = time.monotonic()
            if now - last_check >= interval or pending_sha is not None:
                if now - last_check >= interval:
                    last_check = now
                    try:
                        remote = newest_commit(branch)
                        status = _load_json(root / STATE_NAME, {})
                        installed = status.get("sha") if isinstance(status, dict) else None
                        if remote != installed:
                            if remote != pending_sha:
                                pending_files = fetch_release(remote)
                                pending_sha = remote
                                print(f"\nNEXORA: nova versão {remote[:10]} pronta; aguardando janelas fechadas.", flush=True)
                        else:
                            pending_sha = pending_files = None
                    except (OSError, ValueError, KeyError, urllib.error.URLError,
                            zipfile.BadZipFile) as exc:
                        print(f"NEXORA: sem atualização por enquanto ({type(exc).__name__}).", flush=True)
                if pending_sha is not None and pending_files is not None:
                    try:
                        safe = idle_browser_state() if child.poll() is None else True
                    except (OSError, ValueError, urllib.error.URLError):
                        safe = False
                    if safe:
                        print("NEXORA: nenhum navegador ativo; aplicando atualização...", flush=True)
                        terminate_agent(child)
                        child = None
                        try:
                            install_release(root, pending_files, pending_sha)
                            print("NEXORA: atualização instalada; reiniciando agente.", flush=True)
                            failures = 0
                        except Exception as exc:
                            print(f"NEXORA: atualização falhou ({type(exc).__name__}); "
                                  "arquivos anteriores restaurados.", flush=True)
                        pending_sha = pending_files = None
                        last_check = time.monotonic()
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nNEXORA: encerrando supervisor por solicitação do usuário.", flush=True)
    finally:
        if child is not None:
            terminate_agent(child)


def run(root: Path, *, branch: str = BRANCH, panel: str | None = None,
        interval: int = INTERVAL_SECONDS):
    with single_instance(root):
        return _run_unlocked(root, branch=branch, panel=panel, interval=interval)


def main():
    parser = argparse.ArgumentParser(description="Nexora Windows auto updater")
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--painel", default=None)
    args = parser.parse_args()
    run(Path(__file__).resolve().parent, branch=args.branch, panel=args.painel)


if __name__ == "__main__":
    main()
