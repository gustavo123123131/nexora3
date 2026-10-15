"""Mãe/Filha open Chromium profiles without creating or filling accounts."""
import importlib
import threading

from fastapi.testclient import TestClient
import servidor
import nexora_accounts.proxy_store as proxy_store


def children(node):
    yield node
    for child in node.get("children", []):
        yield from children(child)


def test_both_buttons_open_browsers_without_identity_records(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "proxies.json")
    with TestClient(servidor.app) as client:
        initial = client.get("/api/state").json()
        csrf = {"X-LixoCPA-CSRF": initial["csrf_token"]}
        tabs = initial["tree"]["children"][0]["children"]
        assert len(tabs) == 2
        for label, tab_node in zip(("mae", "filha"), tabs):
            controller = servidor.ctk.get_widget(tab_node["children"][0]["id"])
            module = importlib.import_module(controller.__class__.__module__)
            path = tmp_path / label / "__profiles"
            monkeypatch.setattr(module, "PROFILES_DIR", path)
            finished = threading.Event()
            calls = []

            def fake_run_all(**kwargs):
                calls.append(kwargs)
                finished.set()

            monkeypatch.setattr(module, "run_all", fake_run_all)
            if hasattr(module, "clear_invite_file"):
                monkeypatch.setattr(module, "clear_invite_file", lambda _log: None)
            monkeypatch.setattr(controller, "save_settings", lambda: None)

            originals = {
                "urls": controller.urls_var.get(),
                "proxy": controller.proxy_var.get(),
                "mode": controller.mode_var.get(),
                "count": controller.count_var.get(),
                "delay": controller.delay_var.get(),
            }
            try:
                controller.urls_var.set(" exemplo.com ")
                controller.proxy_var.set("")
                controller.mode_var.set("Computador")
                controller.count_var.set("2")
                controller.delay_var.set("0.4")
                buttons = [
                    node for node in children(tab_node)
                    if node["type"] == "button"
                    and node["options"].get("text") == "Abrir Navegadores"
                ]
                assert len(buttons) == 1
                response = client.post(
                    "/api/event", headers=csrf,
                    json={"id": buttons[0]["id"], "event": "click"}
                )
                assert response.status_code == 200
                assert finished.wait(6)
                assert len(calls) == 1
                call = calls[0]
                assert call["run_auto"] is False
                assert call["automation_callback"] is None
                assert call["instance_plans"] is None
                assert call["keep_profiles"] is True
                assert call["auto_goto"] is True
                assert call["urls_string"] == "https://exemplo.com"
                assert call["count_per_url"] == 2
                assert call["delay_seconds"] == 0.4
                assert call["mode"] == "desktop"
                assert module._build_instance_plans(
                    ["https://exemplo.com"], 2, False
                ) == [
                    {"idx": 1, "url": "https://exemplo.com"},
                    {"idx": 2, "url": "https://exemplo.com"},
                ]
                assert not path.exists()  # No disk data written before browser launch.
                assert not (tmp_path / "Nexora" / "accounts.json").exists()
                assert client.get("/api/registration/accounts").status_code == 404
            finally:
                if controller.worker_thread:
                    controller.worker_thread.join(timeout=2)
                controller.urls_var.set(originals["urls"])
                controller.proxy_var.set(originals["proxy"])
                controller.mode_var.set(originals["mode"])
                controller.count_var.set(originals["count"])
                controller.delay_var.set(originals["delay"])


def test_legacy_automation_entry_is_inert(monkeypatch, tmp_path):
    """Even a legacy Python caller cannot enable the old account automation."""
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "proxies.json")
    with TestClient(servidor.app):
        gui = servidor.init_original_ui()
        for module_name in (
            "Settings.Criar_Mae.gui_tab", "Settings.Criar_Filha.gui_tab"
        ):
            module = importlib.import_module(module_name)
            tab_cls = module.MaeTab if module_name.endswith("Criar_Mae.gui_tab") else module.FilhaTab
            instances = [v for v in vars(gui).values() if isinstance(v, tab_cls)]
            # Tabs live inside CTk hierarchy, not in the MainWindow __dict__.
            # Explicit callback is no longer exported.
            assert not hasattr(tab_cls, "start_with_automation")
            assert not hasattr(tab_cls, "open_reopen_guides_selector")
