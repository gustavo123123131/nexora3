"""Visual-only browser regression checks of the unchanged localhost frontend.

Runs a local no-reload server on a test-only port. Never starts real browsers
from the multi-login controls or sends credentials to external websites.
"""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = 18765
URL = f"http://127.0.0.1:{PORT}"


def wait_for_server(proc):
    for _ in range(80):
        if proc.poll() is not None:
            raise AssertionError("Servidor de teste encerrou inesperadamente")
        try:
            with urlopen(URL + "/api/health", timeout=0.35) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.15)
    raise AssertionError("Servidor de teste não iniciou")


def test_nexora_visual_and_existing_controls(tmp_path):
    server_env = os.environ.copy()
    # Keep real proxy credentials and selections out of all UI test runs.
    server_env["LOCALAPPDATA"] = str(tmp_path)
    server_env["XDG_STATE_HOME"] = str(tmp_path)
    server = subprocess.Popen(
        [sys.executable, "servidor.py", "--no-reload", "--port", str(PORT)],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"),
        env=server_env,
    )
    try:
        wait_for_server(server)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(URL, wait_until="domcontentloaded")
                expect(page.locator(".tab-bar button")).to_have_count(5, timeout=15000)
                expect(page.locator(".brand-wordmark")).to_have_text("NEXORA")
                expect(page.locator(".tab-bar button.active")).to_have_text("Mãe")
                expect(page.get_by_role("button", name="Abrir Navegadores")).to_be_visible()
                expect(page.locator(".ui-action-start:visible")).to_have_count(1)
                assert page.locator(".ctk-tabs").evaluate(
                    "(el) => getComputedStyle(el).display"
                ) == "grid"
                assert page.locator(".workspace-bar").evaluate(
                    "(el) => getComputedStyle(el).height"
                ) == "68px"
                assert page.locator(".tab-bar").bounding_box()["width"] >= 175
                # The header, configuration grid and logs must stack vertically,
                # rather than appearing as two equal columns on wide screens.
                screen = ".tab-content > .ctk-frame > .ctk-frame"
                header_box = page.locator(screen + " > .ctk-frame:first-child").bounding_box()
                settings_box = page.locator(screen + " > .ctk-frame:nth-child(2)").bounding_box()
                logs_box = page.locator(screen + " > .ctk-frame:nth-child(3)").bounding_box()
                assert header_box and settings_box and logs_box
                assert settings_box["y"] >= header_box["y"] + header_box["height"] - 2
                assert logs_box["y"] >= settings_box["y"] + settings_box["height"] - 2
                assert settings_box["width"] > 700
                assert page.locator(".ctk-option").count() > 0
                assert page.locator("input.ctk-entry").count() > 2
                assert page.locator(".ctk-textbox").count() > 0
                # Check CSS signature to reject an incomplete/stale deployment.
                assert page.locator("body").evaluate(
                    "(el) => getComputedStyle(el).backgroundColor"
                ) == "rgb(13, 18, 29)"
                # Editable control continues syncing through original /api/event.
                first_field = page.locator("input.ctk-entry").first
                first_field.fill("https://example.test")
                expect(first_field).to_have_value("https://example.test")
                # Advanced controls remain in Python but must not appear.
                for hidden_label in (
                    "Iniciar (COM automação)", "Iniciar (SEM automação)",
                    "Selecionar instâncias", "Salvar posição das telas",
                    "Reabrir guias", "Verificar Numero", "Proxy Ligado",
                    "Speed", "Iniciar jogo", "Usar CPF Aleatorio",
                    "Manter perfis (__profiles)", "Reutilizar dados",
                ):
                    assert not any(node.is_visible() for node in page.get_by_text(hidden_label, exact=True).all())
                expect(page.locator(".ctk-option:visible")).to_have_count(1)
                expect(page.locator(".ctk-textbox:visible")).to_have_count(1)
                assert page.locator("input.ctk-entry:visible").count() >= 3
                # Alternate tab navigation retains its original event wiring.
                page.locator(".tab-bar button").nth(1).click()
                expect(page.locator(".tab-bar button.active")).to_have_text("Filha")
                expect(page.get_by_role("button", name="Abrir Navegadores")).to_be_visible()
                expect(page.locator(".ui-action-start:visible")).to_have_count(1)
                page.locator(".tab-bar button").nth(0).click()
                expect(page.locator(".tab-bar button.active")).to_have_text("Mãe")
                # Compact layout keeps the existing controls usable at 960 px.
                page.set_viewport_size({"width": 960, "height": 850})
                expect(page.get_by_role("button", name="Abrir Navegadores")).to_be_visible()
                assert page.locator(".tab-content").bounding_box()["width"] > 550
                page.set_viewport_size({"width": 760, "height": 900})
                expect(page.locator(".tab-bar button").first).to_be_visible()
                expect(page.get_by_role("button", name="Abrir Navegadores")).to_be_visible()
                panels = page.locator(screen + " > .ctk-frame:nth-child(2) > .ctk-frame")
                assert panels.count() == 2
                settings_compact = panels.nth(0).bounding_box()
                actions_compact = panels.nth(1).bounding_box()
                assert settings_compact and actions_compact
                assert actions_compact["y"] >= settings_compact["y"] + settings_compact["height"] - 2
                # Screenshots retained in CI artifacts for visual review.
                screenshot_dir = ROOT / "ui-screenshots"
                screenshot_dir.mkdir(exist_ok=True)
                page.screenshot(path=str(screenshot_dir / "nexora-compact.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 900})
                page.screenshot(path=str(screenshot_dir / "nexora-mae.png"), full_page=True)
                page.locator(".tab-bar button").nth(1).click()
                page.screenshot(path=str(screenshot_dir / "nexora-filha.png"), full_page=True)

                # Modern minimal Proxy tab: one line, save, list and delete.
                proxy_posts = []
                page.on("request", lambda request: proxy_posts.append(request.url)
                        if request.method == "POST" and "/api/" in request.url else None)
                page.get_by_role("button", name="Proxy").click()
                expect(page.locator(".tab-bar button.active")).to_have_text("Proxy")
                expect(page.get_by_role("heading", name="Proxy", exact=True)).to_be_visible()
                line = page.get_by_role("textbox", name="Linha completa da proxy")
                expect(line).to_be_visible()
                expect(page.get_by_role("button", name="Adicionar Proxy")).to_be_enabled()
                expect(page.locator(".proxy-simple-empty")).to_be_visible()
                assert proxy_posts == []
                page.screenshot(path=str(screenshot_dir / "nexora-proxy.png"), full_page=True)
                for width in (960, 760, 420):
                    page.set_viewport_size({"width": width, "height": 900})
                    expect(line).to_be_visible()
                    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                    assert overflow <= 2, f"Proxy horizontal overflow at {width}px: {overflow}"
                page.screenshot(path=str(screenshot_dir / "nexora-proxy-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 900})
                line.fill("http://usuario:segredo-nao-exibir@proxy.example.test:8080")
                page.get_by_role("button", name="Adicionar Proxy").click()
                expect(page.locator(".proxy-simple-row")).to_have_count(1)
                expect(page.locator(".proxy-simple-row")).to_contain_text("proxy.example.test:8080")
                assert "segredo-nao-exibir" not in page.locator(".proxy-simple-row").inner_text()
                page.get_by_role("button", name="Excluir").click()
                expect(page.locator(".proxy-simple-empty")).to_be_visible()

                # New Pix tab uses this actual local FastAPI server and
                # a temporary on-disk registry: add, reload, edit and delete.
                page.get_by_role("button", name="Chave Pix").click()
                expect(page.get_by_role("heading", name="Gerenciamento de Chaves Pix")).to_be_visible()
                page.get_by_role("button", name="Adicionar Chave Pix").click()
                pix_input = page.get_by_role("textbox", name="Chave Pix")
                pix_input.fill("CONTATO@EXEMPLO.COM")
                expect(page.locator(".pix-recognition")).to_have_text("Tipo identificado: E-mail")
                page.get_by_role("button", name="Salvar", exact=True).click()
                expect(page.locator(".pix-simple-row")).to_have_count(1)
                expect(page.locator(".pix-simple-row")).to_contain_text("contato@exemplo.com")
                page.reload()
                expect(page.get_by_role("button", name="Chave Pix")).to_be_visible()
                page.get_by_role("button", name="Chave Pix").click()
                expect(page.locator(".pix-simple-row")).to_contain_text("contato@exemplo.com")
                page.get_by_role("button", name="Editar").click()
                page.get_by_role("textbox", name="Chave Pix").fill("novo@exemplo.org")
                page.get_by_role("button", name="Salvar alteração").click()
                expect(page.locator(".pix-simple-row")).to_contain_text("novo@exemplo.org")
                for width in (960, 760, 420):
                    page.set_viewport_size({"width": width, "height": 900})
                    expect(page.locator(".pix-simple-row")).to_be_visible()
                    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                    assert overflow <= 2, f"Pix horizontal overflow at {width}px: {overflow}"
                page.screenshot(path=str(screenshot_dir / "nexora-pix-mobile.png"), full_page=True)
                page.locator(".pix-remove").click()
                expect(page.locator(".pix-simple-empty")).to_be_visible()
                page.set_viewport_size({"width": 1440, "height": 900})

                # Contas tab is preserved and renders minimal cards.
                page.locator(".tab-bar").get_by_role("button", name="Contas", exact=True).click()
                expect(page.get_by_role("heading", name="Contas")).to_be_visible()
                expect(page.locator(".accounts-local-grid")).to_be_visible()
                assert page.locator(".tab-bar").get_by_role("button", name="Backup").count() == 0
                page.locator(".accounts-inner-tabs").get_by_role("button", name="Backup").click()
                expect(page.locator(".accounts-backup-panel")).to_be_visible()
                expect(page.locator(".backup-local")).to_be_visible()
                page.locator(".accounts-inner-tabs").get_by_role("button", name="Contas").click()
                expect(page.locator(".accounts-local-grid")).to_be_visible()
                assert page.get_by_role("button", name="Selecionar").count() == 0
                assert page.get_by_role("button", name="Escolher dispositivo").count() == 0
                for label in ("Mãe", "Filha"):
                    page.get_by_role("button", name=label, exact=True).click()
                    expect(page.get_by_role("button", name="Abrir Navegadores")).to_be_visible()

            finally:
                browser.close()
    finally:
        if server.poll() is None:
            if os.name == "posix":
                os.killpg(os.getpgid(server.pid), signal.SIGTERM)
            else:
                server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
