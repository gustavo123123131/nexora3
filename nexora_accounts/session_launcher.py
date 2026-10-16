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

    def is_running(self, profile_id: int) -> bool:
        with self._lock:
            return profile_id in self._running

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
        runtime = profile.get("runtimeConfig")
        if not isinstance(runtime, dict) or runtime.get("mode") != mode:
            raise ValueError(
                "Esta conta não possui o perfil técnico original registrado. "
                "Não é seguro recriar seu user-agent ou dispositivo automaticamente."
            )
        url = str(profile.get("startUrl") or "").strip()
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("URL salva na conta é inválida.")
        module, tab = self._controller(gui, profile["origin"])
        instance_idx = next(self._instance_numbers)
        stop_event = threading.Event()
        with self._lock:
            if profile_id in self._running:
                raise AccountBusyError(
                    f"{profile['label']} já está aberta. Feche a janela antes de abrir novamente."
                )
            self._running[profile_id] = {"stop": stop_event}

        def log(message):
            # Logs remain visible even if the CTk UI adapter cannot schedule
            # an update; errors no longer disappear behind an HTTP 200.
            safe = str(message)
            try:
                tab.after(0, lambda msg=safe: tab._log(msg))
            except Exception:
                try:
                    tab._log(safe)
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
                bindings = store.profile_bindings()
                assigned_id = bindings.get(str(profile_id))
                connection_mode = profile.get("connectionMode")
                if connection_mode not in ("managed", "direct"):
                    log(
                        f"[Contas] {profile['label']}: modo de conexão original "
                        "indisponível ou legado; não será atribuído outro endereço automaticamente."
                    )
                    return
                if connection_mode == "managed" and not assigned_id:
                    log(f"[Contas] {profile['label']}: vínculo da proxy original ausente; abertura cancelada.")
                    return
                if connection_mode == "direct" and assigned_id:
                    log(f"[Contas] {profile['label']}: vínculo inesperado de proxy; abertura cancelada.")
                    return
                if assigned_id:
                    # A reopened account must NEVER use round-robin fallback.
                    # Deleted/missing bound proxy means a hard failure.
                    selected = next((p for p in listing if p["id"] == assigned_id), None)
                    if selected is None:
                        log(f"[Contas] {profile['label']}: proxy original não disponível; abertura bloqueada.")
                        return
                    result = store.test_authenticated(selected["id"])
                    if not result.get("reachable"):
                        deferred_browser_check = (
                            result.get("status") == "tls_handshake_failed"
                            and result.get("stage") == "tls_destination"
                            and result.get("connectVerified") is True
                            and result.get("browserVerificationRequired") is True
                        )
                        if not deferred_browser_check:
                            log(f"[Contas] {profile['label']}: proxy original inválida ou indisponível ({result.get('status', 'erro')}).")
                            return
                        log(f"[Contas] {profile['label']}: CONNECT aceito; HTTPS e IP serão verificados no Chromium.")
                    session_proxy = ProxyConfig(
                        server=f"{selected['protocol']}://{selected['host']}:{selected['port']}",
                        username=selected.get("username") or None,
                        password=selected.get("password") or None,
                    )
                    session_proxy_id = selected["id"]
                    label = f"{selected['protocol'].upper()} {selected['host']}:{selected['port']}"
                # Direct accounts remain direct even if a proxy is added
                # later. Managed accounts always require the original binding.
                # Never inherit the current Mãe/Filha proxy input.

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
        return {"ok": True, "profileId": profile_id, "deviceMode": mode,
                "message": "Abertura solicitada. Consulte os logs para confirmar a sessão."}


ACCOUNT_LAUNCHER = AccountLauncher()
