#!/usr/bin/env python3
"""Start, monitor and recover the Codespaces web server (stdlib only).

Usage: python .devcontainer/manage.py start|status|stop|restart
This manages ONLY the Codespaces panel (port 8765), not the Windows agent.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".runtime" / "lixocpa"
LOCK = STATE / "supervisor.lock"
LAUNCH_LOCK = STATE / "launch.lock"
SUPERVISOR_PID = STATE / "supervisor.pid"
CHILD_PID = STATE / "server.pid"
LOG = STATE / "server.log"
EVENTS = STATE / "supervisor.log"
PORT = 8765
FAST_FAILURE_SECONDS = 60
MAX_FAST_FAILURES = 3
SHUTDOWN = False


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log_event(message):
    prepare()
    with EVENTS.open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp()} {message}\n")


def health():
    """Verify that port 8765 belongs to *this* application's FastAPI backend."""
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=0.7) as response:
            return response.status == 200 and json.load(response).get("service") == "lixocpa"
    except (OSError, ValueError, TimeoutError):
        return False


def port_is_busy():
    with socket.socket() as client:
        client.settimeout(0.3)
        return client.connect_ex(("127.0.0.1", PORT)) == 0


def read_pid(path):
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def supervisor_pid():
    pid = read_pid(SUPERVISOR_PID)
    if not pid:
        return None
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8")
        if str(Path(__file__).resolve()) not in command or "supervise" not in command:
            return None
        os.kill(pid, 0)
        return pid
    except (OSError, ProcessLookupError):
        return None


@contextlib.contextmanager
def launch_lock():
    prepare()
    with LAUNCH_LOCK.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def start():
    with launch_lock():
        pid = supervisor_pid()
        if health():
            label = f"supervisionado (PID {pid})" if pid else "já ativo (iniciado manualmente)"
            print(f"Painel Lixo CPA já está funcionando: {label}. Nenhuma cópia adicional será iniciada.")
            return 0
        if pid:
            print(f"Supervisor PID {pid} já está tentando recuperar o serviço. Aguarde e consulte o log.")
        elif port_is_busy():
            print("ERRO: a porta 8765 está ocupada por outro serviço. Não iniciei um segundo servidor.", file=sys.stderr)
            return 1
        else:
            rotate_log(LOG)
            with EVENTS.open("a", encoding="utf-8") as stream:
                worker = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "supervise"],
                    cwd=str(ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            print(f"Iniciando supervisor (PID {worker.pid}) e painel 8765...")
        for _ in range(100):
            if health():
                print("Lixo CPA disponível na porta 8765. Abra PORTS > 8765 (Private).")
                return 0
            if port_is_busy() and not supervisor_pid():
                break
            time.sleep(0.2)
        print(f"Servidor ainda indisponível. Veja o diagnóstico em {EVENTS} e {LOG}", file=sys.stderr)
        return 1


def rotate_log(path, limit=4_000_000):
    if path.exists() and path.stat().st_size > limit:
        backup = path.with_suffix(path.suffix + ".1")
        backup.unlink(missing_ok=True)
        path.replace(backup)


def terminate_process_group(pid):
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return


def supervise():
    """One lock-holding process; restart only a few rapid crashes."""
    global SHUTDOWN
    prepare()
    with LOCK.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log_event("Outro supervisor já está ativo. Encerrando supervisor duplicado.")
            return 0

        def stopping(_signum, _frame):
            global SHUTDOWN
            SHUTDOWN = True

        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
        SUPERVISOR_PID.write_text(str(os.getpid()), encoding="utf-8")
        quick_failures = 0
        try:
            while not SHUTDOWN:
                started = time.monotonic()
                with LOG.open("a", encoding="utf-8") as output:
                    child = subprocess.Popen(
                        [sys.executable, "-u", str(ROOT / "servidor.py")],
                        cwd=str(ROOT),
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                CHILD_PID.write_text(str(child.pid), encoding="utf-8")
                log_event(f"Servidor iniciado: PID {child.pid}, porta {PORT}, reload ativo.")
                while child.poll() is None and not SHUTDOWN:
                    time.sleep(0.25)
                if SHUTDOWN:
                    terminate_process_group(child.pid)
                    try:
                        child.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait()
                    log_event("Parada solicitada; servidor encerrado sem reinicialização.")
                    break
                duration = time.monotonic() - started
                log_event(f"Servidor saiu inesperadamente (código {child.returncode}), após {duration:.1f}s.")
                CHILD_PID.unlink(missing_ok=True)
                if duration < FAST_FAILURE_SECONDS:
                    quick_failures += 1
                else:
                    quick_failures = 0
                if quick_failures >= MAX_FAST_FAILURES:
                    log_event(f"Bloqueio de reinicializações: {MAX_FAST_FAILURES} falhas rápidas. Corrija o erro e use 'start'.")
                    break
                wait_seconds = min(8, 2 ** quick_failures)
                log_event(f"Nova tentativa em {wait_seconds}s ({quick_failures}/{MAX_FAST_FAILURES} falhas rápidas).")
                for _ in range(wait_seconds * 4):
                    if SHUTDOWN:
                        break
                    time.sleep(0.25)
        finally:
            CHILD_PID.unlink(missing_ok=True)
            SUPERVISOR_PID.unlink(missing_ok=True)
            log_event("Supervisor encerrado.")
    return 0


def stop():
    with launch_lock():
        pid = supervisor_pid()
        if pid is None:
            if health():
                print("Existe um servidor iniciado fora do supervisor. Não será encerrado automaticamente.")
                return 1
            print("Nenhum supervisor ativo.")
            return 0
        os.kill(pid, signal.SIGTERM)
        for _ in range(90):
            if not supervisor_pid() and not port_is_busy():
                print("Supervisor e servidor parados.")
                return 0
            time.sleep(0.2)
        print("Aguardando encerramento. Consulte os logs e execute 'status'.", file=sys.stderr)
        return 1


def status():
    pid = supervisor_pid()
    if health():
        print(f"Painel ONLINE: porta {PORT}; supervisor: {pid or 'não gerenciado'}")
        return 0
    if pid:
        print(f"Painel indisponível; supervisor PID {pid} tentando recuperar.")
        return 1
    if port_is_busy():
        print(f"Painel indisponível; porta {PORT} ocupada por outro processo.")
    else:
        print("Painel PARADO. Rode: bash .devcontainer/start.sh")
    return 1


def main():
    cli = argparse.ArgumentParser(description="Inicialização e recuperação do painel Codespaces")
    cli.add_argument("action", choices=["start", "status", "stop", "restart", "supervise"])
    action = cli.parse_args().action
    if action == "supervise":
        return supervise()
    if action == "start":
        return start()
    if action == "stop":
        return stop()
    if action == "status":
        return status()
    if action == "restart":
        if stop() != 0:
            return 1
        return start()
    return 2


if __name__ == "__main__":
    sys.exit(main())
