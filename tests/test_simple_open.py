"""Opening Mãe/Filha smoke tests without visiting an external registration site.

Exercises the actual CTk button callback, _start() and run_all() hand-off,
with the browser worker replaced by a recorder. Configuration and profiles are
left untouched, allowing reproducible CI verification of both account tabs.
"""
import importlib
import threading
from fastapi.testclient import TestClient
import servidor
from nexora_accounts import service as account_service
from Settings.device_modes import profile_directory


def children(node):
    yield node
    for child in node.get("children", []):
        yield from children(child)


def test_abrir_conta_generates_local_profile_records_and_isolated_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(account_service, "_default_folder", lambda: tmp_path / "Nexora")
    monkeypatch.setattr(servidor, "_accounts_service",
                        account_service.AccountsService(start_browser=False))
    with TestClient(servidor.app) as client:
        state = client.get("/api/state").json()
        csrf = {"X-LixoCPA-CSRF": state["csrf_token"]}
        tabs = state["tree"]["children"][0]["children"]
        assert len(tabs) == 2
        for label, tab_node in zip(("mae", "filha"), tabs):
            account_node = tab_node["children"][0]
            controller = servidor.ctk.get_widget(account_node["id"])
            assert controller is not None
            module = importlib.import_module(controller.__class__.__module__)
            profile_root = tmp_path / label / "__profiles"
            monkeypatch.setattr(module, "PROFILES_DIR", profile_root)
            finished = threading.Event()
            captured = []

            def fake_run_all(**kwargs):
                captured.append(kwargs)
                finished.set()

            monkeypatch.setattr(module, "run_all", fake_run_all)
            if hasattr(module, "clear_invite_file"):
                monkeypatch.setattr(module, "clear_invite_file", lambda _log: None)
            monkeypatch.setattr(controller, "save_settings", lambda: None)

            previous_url = controller.urls_var.get()
            previous_proxy = controller.proxy_var.get()
            previous_mode = controller.mode_var.get()
            previous_count = controller.count_var.get()
            previous_delay = controller.delay_var.get()
            try:
                controller.urls_var.set("  exemplo.com  ")
                controller.proxy_var.set("")  # No hidden proxy is required to launch.
                controller.count_var.set("2")
                controller.delay_var.set("0.4")
                controller.mode_var.set("Computador")

                visible_buttons = [
                    node for node in children(tab_node)
                    if node["type"] == "button" and node["options"].get("text") == "Abrir Conta"
                ]
                assert len(visible_buttons) == 1, label
                button_id = visible_buttons[0]["id"]
                response = client.post("/api/event", json={
                    "id": button_id, "event": "click",
                }, headers=csrf)
                assert response.status_code == 200
                assert finished.wait(timeout=6), f"{label} não acionou a abertura"
                assert len(captured) == 1
                call = captured[0]
                assert call["run_auto"] is False
                assert call["automation_callback"] is None
                assert len(call["instance_plans"]) == 2
                assert [p["idx"] for p in call["instance_plans"]] == [1, 2]
                assert call["auto_goto"] is True
                for plan in call["instance_plans"]:
                    assert profile_directory(profile_root, call["mode"], plan["idx"]).is_dir()
                records = client.get("/api/registration/accounts").json()["accounts"]
                created = [a for a in records if a["profileScope"] == label]
                assert len(created) == 2
                assert len({a["id"] for a in created}) == 2
                assert len({a["profileData"]["username"] for a in created}) == 2
                assert all(a["profileData"]["isTestData"] for a in created)
                assert all(a["profileData"]["email"].endswith("@example.test") for a in created)
                assert all(a["profileData"]["password"] for a in created)
                assert all(a["profileData"]["cpfVerified"] is False for a in created)
                assert call["urls_string"] == "https://exemplo.com"
                assert controller.urls_var.get() == "https://exemplo.com"
                assert call["mode"] == "desktop"
                assert call["count_per_url"] == 2
                assert call["delay_seconds"] == 0.4
                assert call["proxy"] is None
            finally:
                if controller.worker_thread:
                    controller.worker_thread.join(timeout=2)
                controller.urls_var.set(previous_url)
                controller.proxy_var.set(previous_proxy)
                controller.mode_var.set(previous_mode)
                controller.count_var.set(previous_count)
                controller.delay_var.set(previous_delay)
