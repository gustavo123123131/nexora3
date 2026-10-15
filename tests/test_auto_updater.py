"""Offline regression suite for the self-updating Windows browser agent.

Tests simulate GitHub archives and filesystem state. They never download code,
launch Windows programs or touch the real user's profile directories.
"""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import atualizador_nexora as updater
import servidor


def archive(extra=None):
    files = {
        "servidor.py": b"NEXORA_EDITION = 'browser-only'\n",
        "agente_windows.py": b"# agent\n",
        "atualizador_nexora.py": b"# updater\n",
        "Settings/Criar_Mae/gui_tab.py": "text='Abrir Navegadores'\n".encode(),
        "Settings/Criar_Filha/gui_tab.py": "text='Abrir Navegadores'\n".encode(),
        "webui/static/app.js": b"// updated\n",
        "Settings/Criar_Mae/extensao_recaptcha/LICENSE": b"license text\n",
        "Settings/Criar_Mae/extensao_recaptcha/src/font.woff2": b"fontdata",
    }
    files.update(extra or {})
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, content in files.items():
            bundle.writestr("nexora3-mockcommit/" + path, content)
    return sink.getvalue()


def test_extracts_full_source_only_with_extension_assets():
    files = updater.collect_sources(archive())
    assert "Settings/Criar_Mae/extensao_recaptcha/LICENSE" in files
    assert "Settings/Criar_Mae/extensao_recaptcha/src/font.woff2" in files
    assert files["webui/static/app.js"] == b"// updated\n"


@pytest.mark.parametrize("extra", [
    {"servidor.py": b"not-browser-only"},
    {"Settings/Criar_Mae/gui_tab.py": b"create_launch_profiles"},
    {"nexora_accounts/registration_autofill.py": b"legacy"},
])
def test_refuses_code_that_reintroduces_old_registration(extra):
    with pytest.raises(ValueError):
        updater.collect_sources(archive(extra))


def test_archive_path_traversal_cannot_escape_destination(tmp_path):
    sources = updater.collect_sources(archive({
        "../../outside.py": b"malicious",
        "Settings/__profiles/local-session.py": b"secret",
        "Settings/Criar_Mae/settings.json": b"new settings",
        ".venv/Scripts/python.exe": b"malicious",
    }))
    assert not any("outside.py" in p for p in sources)
    assert not any("__profiles" in p for p in sources)
    assert "Settings/Criar_Mae/settings.json" not in sources
    assert ".venv/Scripts/python.exe" not in sources
    updater.install_release(tmp_path, sources, "a" * 40)
    assert not (tmp_path.parent / "outside.py").exists()


def test_update_preserves_private_profiles_settings_and_credentials(tmp_path):
    kept = {
        "Settings/Criar_Mae/__profiles/android/profile_1/session.dat": b"COOKIE",
        "Settings/Criar_Filha/contas/registros.json": b'{"private": true}',
        "Settings/Criar_Mae/settings.json": b'{"private": true}',
        "nexora_accounts/accounts.json": b"existing records",
        "proxies.json": b"proxy-secret",
        ".venv/Scripts/python.exe": b"PYTHON",
        "logs/agent.log": b"existing logs",
        "nexora-updater-config.json": b'{"panel":"https://example-8765.app.github.dev"}',
        "custom-user-file.py": b"not managed by upstream",
        "nexora_accounts/registration_autofill.py": b"DELETED LEGACY",
    }
    for relative, data in kept.items():
        p = tmp_path / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    sha = "a" * 40
    updater.install_release(
        tmp_path,
        updater.collect_sources(archive({"webui/static/new.css": b"body{}"})),
        sha,
    )
    for rel, content in kept.items():
        if rel == "nexora_accounts/registration_autofill.py":
            assert not (tmp_path / rel).exists()
        else:
            assert (tmp_path / rel).read_bytes() == content
    assert (tmp_path / "webui/static/new.css").read_bytes() == b"body{}"
    assert json.loads((tmp_path / updater.STATE_NAME).read_text())["sha"] == sha
    assert json.loads((tmp_path / updater.MANIFEST_NAME).read_text())


def test_rollback_restores_all_original_code(tmp_path):
    sources = updater.collect_sources(archive())
    managed = tmp_path / "webui/static/app.js"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"original javascript")
    status = tmp_path / updater.STATE_NAME
    status.write_text('{"sha":"old"}', encoding="utf-8")
    with pytest.raises(OSError, match="Falha de escrita simulada"):
        updater.install_release(tmp_path, sources, "b" * 40, fail_after=3)
    assert managed.read_bytes() == b"original javascript"
    assert status.read_text() == '{"sha":"old"}'
    assert not (tmp_path / updater.MANIFEST_NAME).exists()


def test_removed_managed_code_deleted_but_unknown_files_untouched(tmp_path):
    old = tmp_path / "webui/static/deprecated.js"
    old.parent.mkdir(parents=True)
    old.write_text("outdated", encoding="utf-8")
    user = tmp_path / "webui/static/custom-user.js"
    user.write_text("keep", encoding="utf-8")
    (tmp_path / updater.MANIFEST_NAME).write_text(
        json.dumps(["webui/static/deprecated.js"]), encoding="utf-8"
    )
    updater.install_release(tmp_path, updater.collect_sources(archive()), "c" * 40)
    assert not old.exists()
    assert user.read_text() == "keep"


def test_idle_endpoint_refuses_restart_during_browser_sessions(monkeypatch):
    monkeypatch.setattr(servidor, "_GUI", None)
    assert servidor._active_browser_workers() == 0
    dummy = SimpleNamespace(
        worker_thread=SimpleNamespace(is_alive=lambda: True),
        children=[],
    )
    gui = SimpleNamespace(children=[
        SimpleNamespace(children=[dummy]),
        SimpleNamespace(
            worker_thread=SimpleNamespace(is_alive=lambda: False),
            children=[],
        ),
    ])
    monkeypatch.setattr(servidor, "_GUI", gui)
    assert servidor._active_browser_workers() == 1


def test_reject_unapproved_branch_and_malformed_github_sha():
    with pytest.raises(ValueError):
        updater.newest_commit("main")
    with pytest.raises(ValueError):
        updater.fetch_release("../../attack")
