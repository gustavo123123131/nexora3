"""Regression test: local Windows agent parses and uses the configured port."""
import sys
from types import ModuleType
import agente_windows


def test_agente_windows_inicia_com_porta(monkeypatch, capsys):
    # Do not actually serve HTTP or launch Chromium during the test.
    fake_server = ModuleType("servidor")
    fake_server.app = object()
    fake_uvicorn = ModuleType("uvicorn")
    called = {}

    def fake_run(app, host, port, log_level, access_log):
        called.update(app=app, host=host, port=port, log_level=log_level, access_log=access_log)

    fake_uvicorn.run = fake_run
    monkeypatch.setitem(sys.modules, "servidor", fake_server)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(sys, "argv", [
        "agente_windows.py", "--painel",
        "https://studious-sniffle-97jrv6vr4xpg2pj94-8765.app.github.dev",
        "--porta", "8767"
    ])
    agente_windows.main()
    stdout = capsys.readouterr().out
    assert "AGENTE PRONTO" in stdout
    assert "http://127.0.0.1:8767" in stdout
    assert called["app"] is fake_server.app
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8767
    assert called["log_level"] == "warning"
    assert called["access_log"] is False
