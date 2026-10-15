"""Regression checks for deployment of the browser-only Windows agent.

Do not run the PowerShell installer: tests remain offline and never modify
the user's actual Windows directory.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_launcher_stays_on_browser_only_branch():
    launcher = (ROOT / "iniciar-agente-desenvolvimento.cmd").read_text(
        encoding="utf-8"
    )
    assert 'set "NEXORA_DEV_BRANCH=fix/remover-criacao-contas"' in launcher
    assert "fix/remover-criacao-contas/instalar-agente-windows.ps1" in launcher


def test_installer_prunes_registration_code_but_preserves_user_data():
    source = (ROOT / "instalar-agente-windows.ps1").read_text(
        encoding="utf-8"
    )
    assert 'if (Test-AgentPortBusy)' in source
    assert 'if ($Branch -eq "fix/remover-criacao-contas")' in source
    assert 'Remove-Item -LiteralPath $OldCode -Recurse -Force' in source
    assert 'Copy-Item -Path (Join-Path $Source "*") -Destination $AgentDir -Recurse -Force' in source
    assert "registration_autofill.py" in source
    assert "registration_submit.py" in source
    assert "profile_data.py" in source
    assert r"settings\actions.py" in source
    assert r"settings\identity.py" in source
    assert "accounts_view.js" in source
    assert "proxybet_registration_bridge" in source
    assert "__profiles/" in source  # explicit preserve rule
    assert "accounts.json" in source
    assert "registr" in source
    assert "versao-instalada.txt" in source
    assert "iniciar-agente-local.cmd" in source


def test_web_panel_rejects_old_agent():
    js = (ROOT / "webui/static/app.js").read_text(encoding="utf-8")
    assert "data.edition !== 'browser-only'" in js
    assert "Agente Windows antigo" in js
    assert "Agente Windows desatualizado" in js


def test_no_live_registration_modules_or_call_sites():
    forbidden_paths = [
        "nexora_accounts/profile_data.py",
        "nexora_accounts/registration_autofill.py",
        "nexora_accounts/registration_submit.py",
        "nexora_accounts/service.py",
        "Settings/Criar_Mae/settings/actions.py",
        "Settings/Criar_Filha/settings/actions.py",
        "webui/static/accounts_view.js",
    ]
    for relative in forbidden_paths:
        assert not (ROOT / relative).exists(), relative
    for name in ("Criar_Mae", "Criar_Filha"):
        body = (ROOT / "Settings" / name / "gui_tab.py").read_text(
            encoding="utf-8"
        )
        assert "Abrir Navegadores" in body
        for token in (
            "create_launch_profiles", "autofill_existing_registration",
            "request_autosubmit_after_fill", "RegistrationWatcher",
        ):
            assert token not in body
    server = (ROOT / "servidor.py").read_text(encoding="utf-8")
    assert "'edition': NEXORA_EDITION" in server
    assert "/api/registration/" not in server
