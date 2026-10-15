"""UI -> original Abrir Conta callback -> JSON -> Contas readback.

Runs in a disposable agent subprocess; replaces only run_all to prevent
external navigation and real Chromium launches. Uses the actual FastAPI,
CustomTkinter bridge and JSON account repository.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = 18766
URL = "http://127.0.0.1:18766"


def test_abrir_conta_appears_in_contas_from_same_real_local_json(tmp_path):
    boot = """
import os
from pathlib import Path
import servidor
servidor.init_original_ui()
import Settings.Criar_Mae.gui_tab as mae
import Settings.Criar_Filha.gui_tab as filha
mae.PROFILES_DIR=Path(os.environ['NEXORA_E2E_ROOT'])/'mae'
filha.PROFILES_DIR=Path(os.environ['NEXORA_E2E_ROOT'])/'filha'
mae.run_all=lambda **kwargs: None
filha.run_all=lambda **kwargs: None
import uvicorn
uvicorn.run(servidor.app,host='127.0.0.1',port=18766,log_level='warning')
"""
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path),
               NEXORA_E2E_ROOT=str(tmp_path / "profiles"))
    proc = subprocess.Popen(
        [sys.executable, "-c", boot], cwd=ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"))
    try:
        for attempt in range(100):
            if proc.poll() is not None:
                raise AssertionError("Agente de teste não iniciou")
            try:
                with urlopen(URL + "/api/health", timeout=.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError("Agente de teste indisponível")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(URL)
                expect(page.get_by_role("button", name="Abrir Conta")).to_be_visible()
                page.get_by_role("button", name="Abrir Conta").click()
                page.wait_for_function("""async () => {
                    const result = await fetch('/api/registration/accounts');
                    if (!result.ok) return false;
                    return (await result.json()).accounts.some(a => a.profileScope === 'mae');
                }""", timeout=15000)
                page.get_by_role("button", name="Contas", exact=True).click()
                expect(page.locator(".accounts-card")).to_have_count(1)
                expect(page.locator(".accounts-list")).to_contain_text("Conta Mãe #1")
                page.get_by_role("button", name="Ver detalhes").click()
                expect(page.get_by_text("CPF fictício não verificado")).to_be_visible()
                expect(page.get_by_role("textbox", name="Senha gerada")).to_have_attribute(
                    "type", "password")
            finally:
                browser.close()
        records = tmp_path / "Nexora" / "accounts.json"
        assert records.exists()
        import json
        assert len(json.loads(records.read_text(encoding="utf-8"))["accounts"]) == 1
        assert (tmp_path / "profiles" / "mae" / "profile_1").exists()
    finally:
        if proc.poll() is None:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=7)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
