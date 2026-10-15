"""Mãe/Filha create one local profile per click and launch browsers."""
import importlib
import threading

from fastapi.testclient import TestClient
import servidor
import nexora_accounts.proxy_store as proxy_store
import nexora_accounts.local_profiles as local_profiles


def children(node):
    yield node
    for child in node.get("children", []):
        yield from children(child)


def test_both_buttons_open_browsers_and_create_one_local_profile_each(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "proxies.json")
    monkeypatch.setattr(local_profiles, "_default_path", lambda: tmp_path / "Nexora" / "generated_profiles.json")
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
                assert not any(node["options"].get("text") == "Abrir Conta" for node in children(tab_node))
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
                plans = call["instance_plans"]
                assert len(plans) == 2
                assert plans[0]["idx"] == 1
                assert plans[0]["url"] == "https://exemplo.com"
                assert plans[0]["registration_profile"]["origin"] == label
                assert plans[0]["registration_profile"]["password"] == plans[0]["registration_profile"]["confirmPassword"]
                assert plans[1]["idx"] == 2
                assert plans[1]["url"] == "https://exemplo.com"
                assert plans[0]["account_profile"]["id"] != plans[1]["account_profile"]["id"]
                for planned in plans:
                    assert planned["account_profile"] == planned["registration_profile"]
                    assert planned["device_mode"] == "desktop"
                    assert planned["account_profile"]["deviceMode"] == "desktop"
                    assert planned["account_profile"]["startUrl"] == "https://exemplo.com"
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
                assert not path.exists()  # Browser directories are created only by Chromium.
                records = client.get("/api/local-profiles").json()["profiles"]
                assert len(records) == (2 if label == "mae" else 4)
                assert all(p["origin"] == label for p in records[-2:])
                assert records[-1]["deviceMode"] == "desktop"
                assert records[-1]["label"] == f"Conta {len(records)}"
                assert records[-1]["password"] == records[-1]["confirmPassword"]
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



def test_saved_proxy_pool_is_required_and_assigned_per_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "proxies.json")
    monkeypatch.setattr(local_profiles, "_default_path",
                        lambda: tmp_path / "generated_profiles.json")
    store = proxy_store.ProxyStore()
    first = store.save_line("http://userA:pwA@one.test:8881")
    second = store.save_line("socks5://userB:pwB@two.test:8882")
    monkeypatch.setattr(proxy_store.ProxyStore, "test_authenticated",
                        lambda self, identifier, **kwargs: {"reachable": True, "status": "ok"})
    with TestClient(servidor.app) as client:
        initial = client.get("/api/state").json()
        csrf = {"X-LixoCPA-CSRF": initial["csrf_token"]}
        tab = initial["tree"]["children"][0]["children"][0]
        ctrl = servidor.ctk.get_widget(tab["children"][0]["id"])
        module = importlib.import_module(ctrl.__class__.__module__)
        monkeypatch.setattr(ctrl, "save_settings", lambda: None)
        monkeypatch.setattr(module, "clear_invite_file", lambda _log: None)
        completed = threading.Event()
        calls = []

        def pretend_launch(**kwargs):
            calls.append(kwargs)
            completed.set()

        monkeypatch.setattr(module, "run_all", pretend_launch)
        original = (ctrl.urls_var.get(), ctrl.count_var.get(), ctrl.proxy_var.get())
        try:
            ctrl.urls_var.set("example.com")
            ctrl.count_var.set("2")
            ctrl.proxy_var.set("not-a-real-legacy-proxy")
            button = next(node for node in children(tab)
                          if node["type"] == "button"
                          and node["options"].get("text") == "Abrir Navegadores")
            response = client.post("/api/event", headers=csrf,
                                   json={"id": button["id"], "event": "click"})
            assert response.status_code == 200
            assert completed.wait(6)
            ctrl.worker_thread.join(timeout=2)
            assert len(calls) == 1
            plans = calls[0]["instance_plans"]
            assert [p["proxy_config"].server for p in plans] == [
                "http://one.test:8881", "socks5://two.test:8882",
            ]
            assert [p["proxy_config"].username for p in plans] == ["userA", "userB"]
            assert calls[0]["proxy"] is None
            profile_id = plans[0]["registration_profile"]["id"]
            assert store.profile_bindings()[str(profile_id)] == first["id"]

            # No direct opening or new account when a required proxy fails.
            monkeypatch.setattr(proxy_store.ProxyStore, "test_authenticated",
                                lambda self, identifier, **kwargs: {
                                    "reachable": False, "status": "auth_rejected",
                                    "message": "Autenticação recusada"
                                })
            ctrl.start_browsers()
            assert len(calls) == 1
        finally:
            ctrl.urls_var.set(original[0])
            ctrl.count_var.set(original[1])
            ctrl.proxy_var.set(original[2])



def test_tls_probe_eof_defers_only_to_chromium_without_direct_fallback(monkeypatch, tmp_path):
    """An authenticated CONNECT may be retested in the real browser."""
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "proxies.json")
    monkeypatch.setattr(local_profiles, "_default_path",
                        lambda: tmp_path / "Nexora" / "generated_profiles.json")
    store = proxy_store.ProxyStore()
    store.save_line("http://user:password@proxy.example.com:8000")
    monkeypatch.setattr(
        proxy_store.ProxyStore, "test_authenticated",
        lambda self, identifier, **kw: {
            "reachable": False, "status": "tls_handshake_failed",
            "stage": "tls_destination", "connectVerified": True,
            "browserVerificationRequired": True,
        },
    )

    with TestClient(servidor.app):
        gui = servidor.init_original_ui()
        from Settings.Criar_Mae.gui_tab import MaeTab
        module = importlib.import_module(MaeTab.__module__)
        tab = next(node for node in children_widgets(gui) if isinstance(node, MaeTab))
        monkeypatch.setattr(tab, "save_settings", lambda: None)
        monkeypatch.setattr(module, "clear_invite_file", lambda log: None)
        seen = []
        completed = threading.Event()

        def fake_run_all(**kwargs):
            seen.append(kwargs)
            completed.set()

        monkeypatch.setattr(module, "run_all", fake_run_all)
        old = (tab.urls_var.get(), tab.mode_var.get(), tab.count_var.get())
        try:
            tab.urls_var.set("https://example.org")
            tab.mode_var.set("Computador")
            tab.count_var.set("1")
            tab.start_browsers()
            assert completed.wait(5)
            assert len(seen) == 1
            plans = seen[0]["instance_plans"]
            assert len(plans) == 1
            assert plans[0]["proxy_config"].username == "user"
            assert seen[0]["proxy"] is None
        finally:
            tab.urls_var.set(old[0])
            tab.mode_var.set(old[1])
            tab.count_var.set(old[2])


def children_widgets(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from children_widgets(child)


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
