"""End-to-end server lifecycle tests: start, idempotence, crash recovery and stop.

Runs only against the lightweight /api/health route. No logins or automations.
"""
import importlib.util
import os
from pathlib import Path
import signal
import time
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".devcontainer" / "manage.py"
SPEC = importlib.util.spec_from_file_location("lixocpa_manage", SCRIPT)
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.mark.skipif(os.name != "posix", reason="Linux Codespaces supervisor only")
def test_codespaces_service_start_stop_recovery():
    # Github Actions has no other app running on port 8765.
    assert not manager.port_is_busy(), "porta 8765 deve estar livre para o teste isolado"
    try:
        assert manager.start() == 0
        assert manager.health()
        supervisor = manager.supervisor_pid()
        assert supervisor is not None

        # Calling start repeatedly must not spawn another supervisor.
        assert manager.start() == 0
        assert manager.supervisor_pid() == supervisor

        # Unexpectedly kill Uvicorn/reloader process group.
        child_pid = manager.read_pid(manager.CHILD_PID)
        assert child_pid is not None
        os.killpg(os.getpgid(child_pid), signal.SIGKILL)

        # The supervisor must restore the health endpoint on its own.
        recovered = False
        for _ in range(80):
            if (manager.health() and
                    manager.read_pid(manager.CHILD_PID) != child_pid):
                recovered = True
                break
            time.sleep(0.25)
        assert recovered, manager.EVENTS.read_text()[-2000:]
        # Confirm the actual browser entry page is back, not just health.
        with urlopen("http://127.0.0.1:8765/", timeout=3) as response:
            assert response.status == 200
            assert b"application" in response.read()
        assert manager.supervisor_pid() == supervisor
    finally:
        manager.stop()
    for _ in range(40):
        if not manager.port_is_busy():
            break
        time.sleep(0.25)
    assert not manager.health()


def test_crash_loop_is_bounded_without_launching_browser(tmp_path, monkeypatch):
    """A broken configuration must stop after at most three quick failures."""
    state = tmp_path / "runtime"
    monkeypatch.setattr(manager, "STATE", state)
    monkeypatch.setattr(manager, "LOCK", state / "supervisor.lock")
    monkeypatch.setattr(manager, "SUPERVISOR_PID", state / "supervisor.pid")
    monkeypatch.setattr(manager, "CHILD_PID", state / "server.pid")
    monkeypatch.setattr(manager, "LOG", state / "server.log")
    monkeypatch.setattr(manager, "EVENTS", state / "supervisor.log")
    monkeypatch.setattr(manager.time, "sleep", lambda _seconds: None)
    calls = []

    class BrokenBackend:
        pid = os.getpid()
        returncode = 1

        def poll(self):
            return 1

    def launch(*args, **kwargs):
        calls.append(args)
        return BrokenBackend()

    monkeypatch.setattr(manager.subprocess, "Popen", launch)
    monkeypatch.setattr(manager, "SHUTDOWN", False)
    assert manager.supervise() == 0
    assert len(calls) == manager.MAX_FAST_FAILURES == 3
    assert "Bloqueio de reinicializações" in manager.EVENTS.read_text()
    assert not manager.SUPERVISOR_PID.exists()
    assert not manager.CHILD_PID.exists()
