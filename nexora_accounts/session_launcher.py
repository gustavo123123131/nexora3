"""Launch an existing Nexora account with its own Chromium profile.

A card's ID is resolved independently of the legacy global selection. No
registration resubmission occurs when reopening an already saved account.
"""
from __future__ import annotations

import importlib
import itertools
import threading

from nexora_accounts.local_profiles import LocalProfileStore
from nexora_accounts.proxy_store import ProxyStore
from Settings.device_modes import profile_mode, resolve_mode
from Settings.Criar_Mae.settings.browser import ProxyConfig


class AccountBusyError(ValueError):
    pass


class AccountLauncher:
    def __init__(self):
        self._lock = threading.RLock()
        self._running = {}
        self._instance_numbers = itertools.count(2000001)

    def active_count(self):
        with self._lock:
            return len(self._running)

    def stop_all(self):
        with self._lock:
            active = list(self._running.values())
        for item in active:
            item["stop"].set()

    @staticmethod
    def _controller(gui, origin):
        module_name, cls_name = (
            ("Settings.Criar_Mae.gui_tab", "MaeTab") if origin == "mae"
            else ("Settings.Criar_Filha.gui_tab", "FilhaTab")
        )
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name)
        pending = [gui]
        while pending:
            widget = pending.pop()
            if isinstance(widget, cls):
                return module, widget
            pending.extend(getattr(widget, "children", ()))
        raise ValueError("Aba da conta não está disponível no agente.")

    def launch(self, gui, profile_id: int):
        """Reserve a profile and start one independent browser worker."""
        profile = LocalProfileStore().get(profile_id)
        from nexora_accounts.browser_sessions import is_account_open
        if is_account_open(profile["origin"], profile_id):
            raise AccountBusyError(
                f"{profile['label']} já está aberta. Feche a janela antes de abrir outra."
            )
        module, tab = self._controller(gui, profile["origin"])
        # Legacy account cards were created before device and URL were saved.
        # Never guess which original URL/device they used.
        device = profile.get("deviceMode")
        if device is None:
            raise ValueError(
                "Esta conta antiga não tem dispositivo salvo. Crie uma nova "
                "conta para poder reabri-la com isolamento garantido."
            )
        mode = resolve_mode(device)
        if mode == "mixed":
            raise ValueError("Dispositivo desta conta não é individual.")
        url = str(profile.get("startUrl") or "").strip()
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("URL salva na conta é inválida.")
        instance_idx = next(self._instance_numbers)
        stop_event = threading.Event()
        with self._lock:
            if profile_id in self._running:
                raise AccountBusyError(
                    f"{profile['label']} já está aberta. Feche a janela antes de abrir novamente."
                )
            self._running[profile_id] = {"stop": stop_event}

        def log(message):
            try:
                tab.after(0, lambda msg=message: tab._log(msg))
            except Exception:
                pass

        def worker():
            try:
                store = ProxyStore()
                listing = store.list()["proxies"]
                managed = bool(listing) or store.is_managed()
                session_proxy = None
                session_proxy_id = None
                label = ""
                if managed:
                    if not listing:
                        log(f"[Contas] {profile['label']}: não há proxies salvas; abertura bloqueada.")
                        return
                    allocated = store.assign(1, profile_id=profile_id)
                    if not allocated:
                        log(f"[Contas] {profile['label']}: proxy indisponível.")
                        return
                    selected = allocated[0]
                    result = store.test_authenticated(selected["id"])
                    if not result.get("reachable"):
                        log(
                            f"[Contas] {profile['label']}: proxy não validada "
                            f"({result.get('status', 'erro')}); navegador não iniciado."
                        )
                        return
                    session_proxy = ProxyConfig(
                        server=f"{selected['protocol']}://{selected['host']}:{selected['port']}",
                        username=selected.get("username") or None,
                        password=selected.get("password") or None,
                    )
                    session_proxy_id = selected["id"]
                    label = f"{selected['protocol'].upper()} {selected['host']}:{selected['port']}"
                    if not store.profile_bindings().get(str(profile_id)):
                        store.bind_profile(profile_id, session_proxy_id)
                else:
                    # If the local proxy workspace has never been configured,
                    # retain the original legacy proxy behavior on this tab.
                    session_proxy = tab._validate_proxy()
                    if str(tab.proxy_var.get() or "").strip() and session_proxy is None:
                        log(f"[Contas] {profile['label']}: configuração de proxy inválida; abertura cancelada.")
                        return

                plan = {
                    "idx": instance_idx,
                    "url": url or "about:blank",
                    "device_mode": mode,
                    "account_profile": profile,
                }
                if session_proxy_id:
                    plan["proxy_config"] = session_proxy
                    plan["proxy_id"] = session_proxy_id
                    plan["proxy_label"] = label
                # Reopen only: registration_profile is NOT set, so opening
                # a saved card never submits a second registration.
                log(f"[Contas] Abrindo {profile['label']} — {mode}; navegador isolado.")
                module.run_all(
                    urls_string=url,
                    mode=mode,
                    count_per_url=1,
                    headless=bool(tab.headless_var.get()),
                    proxy=session_proxy,
                    auto_goto=bool(url),
                    keep_profiles=True,
                    run_auto=False,
                    start_game_enabled=False,
                    start_game_name="",
                    start_game_id="",
                    stop_event=stop_event,
                    winpos_controller=tab.winpos_controller,
                    speed_controller=tab.speed_controller,
                    verify_phone_controller=tab.verify_phone_controller,
                    mirror=tab.mirror,
                    withdraw_controller=tab.withdraw_controller,
                    game_launch_controller=tab.game_launch_controller,
                    instance_target_controller=tab.instance_target_controller,
                    log=log,
                    delay_seconds=0,
                    use_same_identity=False,
                    instance_plans=[plan],
                    automation_callback=None,
                )
            except Exception as exc:
                log(
                    f"[Contas] {profile['label']}: não foi possível iniciar "
                    f"({type(exc).__name__})."
                )
            finally:
                with self._lock:
                    self._running.pop(profile_id, None)

        try:
            thread = threading.Thread(
                target=worker, name=f"account-{profile_id}", daemon=True,
            )
            thread.start()
        except Exception:
            with self._lock:
                self._running.pop(profile_id, None)
            raise
        return {"ok": True, "profileId": profile_id, "deviceMode": mode}


ACCOUNT_LAUNCHER = AccountLauncher()
