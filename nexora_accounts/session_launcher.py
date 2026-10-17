"""Launch an existing Nexora account with its own Chromium profile.

A card's ID is resolved independently of the legacy global selection. No
registration resubmission occurs when reopening an already saved account.
"""
from __future__ import annotations

import importlib
import itertools
import threading
import uuid
import queue
from urllib.parse import urlsplit

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
        self._results = {}
        self._instance_numbers = itertools.count(2000001)

    def active_count(self):
        with self._lock:
            return len(self._running)

    def is_running(self, profile_id: int) -> bool:
        with self._lock:
            return profile_id in self._running

    def state(self, profile_id: int) -> dict:
        with self._lock:
            return dict(self._results.get(profile_id, {
                "state": "idle", "message": "Nenhuma abertura solicitada."
            }))

    def _set_state(self, profile_id: int, state: str, message: str):
        with self._lock:
            self._results[profile_id] = {"state": state, "message": message[:220]}


    @staticmethod
    def _probe_existing_page(page):
        """Read-only provider health using the saved Chromium's native client.

        Never export auth headers, tokens, cookies or response bodies.
        A success proves API reachability, not that the provider UID was checked.
        """
        try:
            current = urlsplit(str(page.url or ""))
            if current.scheme != "https" or current.hostname not in ("5gbet.com", "www.5gbet.com"):
                return {"state": "wrong-site", "message": "Abra a plataforma suportada neste navegador."}
            outcome = page.evaluate(r"""async () => {
              const urls=[
                ...performance.getEntriesByType("resource").map(x=>x.name),
                ...Array.from(document.querySelectorAll("script[src]"),x=>x.src)
              ];
              const sdk=urls.find(x=>/\/commonChunk\.[^/?]+\.js(?:[?#]|$)/i.test(x));
              if (!sdk) return {state:"missing-sdk"};
              try {
                const module=await import(sdk);
                if(typeof module.n?.request!=="function")return {state:"missing-sdk"};
                const response=await module.n.request({
                  method:"POST",url:"/api/member/user/security/status"
                },{merge:false,maxRetryCount:0,cache:{useCache:false}});
                return {state:Number(response?.data?.code)===1?"api-reachable":"provider-rejected"};
              } catch {
                return {state:"provider-unavailable"};
              }
            }""")
            state = outcome.get("state") if isinstance(outcome, dict) else None
            messages = {
                "api-reachable": "API de segurança respondeu nesta sessão. UID ainda requer validação.",
                "missing-sdk": "Cliente HTTP original não foi carregado nesta página.",
                "provider-rejected": "A plataforma não confirmou a consulta de segurança.",
                "provider-unavailable": "A consulta à plataforma falhou nesta sessão.",
            }
            if state not in messages:
                state = "invalid-result"
            return {"state": state, "message": messages.get(state, "Resposta inesperada da sessão.")}
        except Exception:
            return {"state": "browser-error", "message": "Navegador indisponível; confira a sessão aberta."}

    def check_pix_browser_session(self, profile_id: int, account_id: str, *, timeout=12) -> dict:
        """Dispatch on the owning Playwright thread only, never a new browser."""
        from nexora_accounts.local_profiles import LocalProfileError
        profile = LocalProfileStore().public_detail(profile_id)
        if profile.get("accountId") != account_id:
            raise LocalProfileError("A conta selecionada mudou. Atualize Operações.")
        # A passive health check may diagnose an OBSERVED ID. It must never
        # promote that candidate to a verified identity or submit a Pix key.
        verified = bool(profile.get("platformUserId"))
        observed = bool(profile.get("platformObservedUserId"))
        if not verified and not observed:
            return {"state": "unverified-id", "message": "Nenhum ID da plataforma observado; abra a conta autenticada."}
        from nexora_accounts.browser_sessions import active_account_session
        # One account-bound broker receives workers opened by Mãe, Filha or
        # Contas. Do not require the Contas launcher's private _running entry.
        live = active_account_session(profile.get("origin"), profile_id, account_id)
        if live is not None:
            if live["stop"].is_set():
                return {"state": "not-running", "message": "A sessão selecionada já foi encerrada."}
            if not live["ready"]:
                return {"state": "starting", "message": "O navegador da conta ainda está inicializando."}
            requests = live["requests"]
        else:
            # Compatibility with existing launcher workers and mock sessions.
            # This fallback is never used for a different profile/account UUID.
            with self._lock:
                info = self._running.get(profile_id)
                if (
                    not info or info["stop"].is_set()
                    or self._results.get(profile_id, {}).get("state") != "ready"
                ):
                    return {
                        "state": "not-running",
                        "message": (
                            "Não encontrei uma sessão ativa vinculada a esta conta. "
                            "Abra a conta pela aba Mãe, Filha ou Contas e aguarde iniciar."
                        ),
                    }
                requests = info["requests"]
        response = queue.Queue(maxsize=1)
        job = {"accountId": account_id, "profileId": profile_id, "result": response}
        try:
            requests.put_nowait(job)
        except queue.Full:
            return {"state": "busy", "message": "Há uma verificação em andamento nesta conta."}
        try:
            result = response.get(timeout=timeout)
        except queue.Empty:
            result = {"state": "timeout", "message": "A sessão não respondeu a tempo; nenhum cadastro foi enviado."}
        if not verified:
            # Explicitly distinguish read-only reachability from authenticated
            # identity verification; never set platformUserId here.
            result = dict(result)
            result["message"] = (
                str(result.get("message") or "Consulta concluída.") +
                " ID observado ainda não verificado; cadastro Pix permanece bloqueado."
            )
            result["identityStatus"] = "observed"
        return result

    def verify_platform_identity(self, profile_id: int, account_id: str, *, timeout=13) -> dict:
        """Query current authenticated account info in the SAME open Chromium.

        Unlike check-session, this cannot fall back to a launcher state alone;
        both the account UUID and run UUID must identify the owning worker.
        """
        from nexora_accounts.local_profiles import LocalProfileError
        from nexora_accounts.browser_sessions import active_account_session
        if type(profile_id) is not int or not isinstance(account_id, str):
            raise LocalProfileError("Conta inválida.")
        profile = LocalProfileStore().public_detail(profile_id)
        if profile.get("accountId") != account_id:
            raise LocalProfileError("Conta selecionada não corresponde ao perfil.")
        if profile.get("platformUserId"):
            return {"state": "already-verified", "message": "O ID desta conta já está verificado."}
        if not profile.get("platformObservedUserId"):
            return {"state": "no-candidate", "message": "Nenhum ID observado nesta conta; autentique-a no site."}
        session = active_account_session(profile.get("origin"), profile_id, account_id)
        if session is None or session["stop"].is_set():
            return {"state": "not-running", "message": "Nenhum navegador vinculado a esta conta está aberto."}
        if not session.get("ready"):
            return {"state": "starting", "message": "O Chromium desta conta ainda está inicializando."}
        run_id = session.get("runId")
        if not run_id or run_id != profile.get("lastSessionId"):
            return {"state": "stale-session", "message": "Sessão da conta divergente; não verificamos o ID."}
        response = queue.Queue(maxsize=1)
        job = {"kind": "verify-identity", "profileId": profile_id,
               "accountId": account_id, "runId": run_id, "result": response}
        try:
            session["requests"].put_nowait(job)
        except queue.Full:
            return {"state": "busy", "message": "A sessão está ocupada. Tente novamente mais tarde."}
        try:
            return response.get(timeout=timeout)
        except queue.Empty:
            return {"state": "timeout", "message": "A verificação demorou; nenhum ID foi confirmado."}

    def _verify_identity_on_owner_thread(self, profile_id: int, account_id: str,
                                         run_id: str, page) -> dict:
        """Persist only when a provider response proves BOTH UID and username."""
        from nexora_accounts.local_profiles import LocalProfileError
        from nexora_accounts.platform_identity_probe import (
            check_auth_identity_page, OFFICIAL_HOSTS, VERIFICATION_SOURCE
        )
        from Settings.platform_identity import VerifiedPlatformIdentity, site_origin
        from urllib.parse import urlsplit
        try:
            profile = LocalProfileStore().public_detail(profile_id)
            if (profile.get("accountId") != account_id or
                profile.get("lastSessionId") != run_id):
                return {"state": "stale-session",
                        "message": "Execução diferente da conta selecionada."}
            observed = profile.get("platformObservedUserId")
            if not observed:
                return {"state": "no-candidate", "message": "ID observado indisponível."}
            expected_site = site_origin(profile.get("startUrl"))
            active_site = site_origin(str(page.url or ""))
            if (urlsplit(expected_site).hostname not in OFFICIAL_HOSTS
                or urlsplit(active_site).hostname not in OFFICIAL_HOSTS
                or not expected_site.startswith("https://")
                or not active_site.startswith("https://")):
                return {"state": "wrong-origin", "message": "Esta conta não está na origem oficial esperada."}
            check = check_auth_identity_page(
                page, observed_uid=observed, username=profile.get("username", ""),
            )
            state = check["state"]
            if state != "matched":
                statuses = {
                    "schema-unconfirmed": "A API respondeu, mas não trouxe UID e username juntos em formato verificado. O ID continua observado.",
                    "uid-mismatch": "O ID devolvido pela API não corresponde ao observado. Nada foi salvo.",
                    "username-mismatch": "O username autenticado não corresponde à conta selecionada. Nada foi salvo.",
                    "wrong-origin": "O navegador não está na plataforma oficial.",
                    "missing-sdk": "Abra uma página autenticada da 5gbet para carregar o cliente original.",
                    "timeout": "A consulta de identidade excedeu o tempo; nenhum ID foi verificado.",
                    "provider-unavailable": "Não foi possível consultar os dados autenticados do usuário.",
                    "provider-rejected": "A plataforma rejeitou a consulta de perfil do usuário.",
                    "invalid-identity-shape": "Resposta de identidade inválida; nenhuma atualização realizada.",
                }
                return {"state": state, "message": statuses.get(
                    state, "Não há prova autenticada suficiente para verificar o ID."
                )}
            # Ensure the observed candidate did not change DURING the request.
            latest = LocalProfileStore().public_detail(profile_id)
            if (latest.get("accountId") != account_id or
                latest.get("lastSessionId") != run_id or
                latest.get("platformObservedUserId") != observed):
                return {"state": "stale-session",
                        "message": "A identificação da conta mudou durante a consulta."}
            evidence = VerifiedPlatformIdentity(
                user_id=observed, source=VERIFICATION_SOURCE,
                profile_id=profile_id, account_id=account_id,
                run_id=run_id, platform_origin=expected_site,
            )
            try:
                LocalProfileStore().save_verified_platform_id(
                    profile_id, account_id, run_id, site_origin=expected_site,
                    platform_user_id=observed,
                    platform_id_source=VERIFICATION_SOURCE,
                    identity_evidence=evidence,
                )
            except LocalProfileError:
                return {"state": "persistence-blocked",
                        "message": "A API confirmou os dados, mas o estado do cadastro não permite salvar o ID automaticamente."}
            return {"state": "verified",
                    "message": "Identidade da conta confirmada pela resposta autenticada da plataforma e salva."}
        except Exception:
            return {"state": "browser-error",
                    "message": "Verificação de identidade indisponível nesta sessão."}


    def bind_pix_key(self, profile_id: int, account_id: str, key_id: str,
                     key_type: str, *, timeout=50):
        """Run a single Pix request on THIS account's existing Chromium worker."""
        from nexora_accounts.browser_sessions import active_account_session
        from nexora_accounts.local_profiles import LocalProfileError
        from nexora_accounts.pix_operations import PixOperationStore, PixPreparationError
        if type(profile_id) is not int or not isinstance(account_id, str):
            raise PixPreparationError("Conta selecionada inválida.")
        profile = LocalProfileStore().public_detail(profile_id)
        if profile.get("accountId") != account_id:
            raise PixPreparationError("Conta selecionada divergente.")
        session = active_account_session(profile.get("origin"), profile_id, account_id)
        if session is None or session["stop"].is_set():
            raise PixPreparationError("Abra o Chromium desta conta para cadastrar a chave Pix.")
        if not session.get("ready"):
            raise PixPreparationError("O navegador da conta ainda está iniciando.")
        run_id = session.get("runId")
        if not run_id or run_id != profile.get("lastSessionId"):
            raise PixPreparationError("Sessão Chromium divergente da conta selecionada.")
        store = PixOperationStore()
        reservation = store.begin_remote(
            profile_id=profile_id, account_id=account_id,
            key_id=key_id, key_type=key_type, run_id=run_id,
        )
        operation = reservation["operation"]
        if reservation["reused"]:
            return {"operation": operation, "reused": True}
        reply = queue.Queue(maxsize=1)
        job = {"kind": "pix-bind", "profileId": profile_id,
               "accountId": account_id, "runId": run_id,
               "operationId": operation["operationId"],
               "result": reply}
        try:
            session["requests"].put_nowait(job)
        except queue.Full:
            operation = store.finish_remote(
                operation["operationId"], profile_id=profile_id,
                account_id=account_id, run_id=run_id,
                result={"state": "failed", "stage": "client"},
            )
            raise PixPreparationError("A sessão da conta está ocupada. Tente mais tarde.")
        try:
            operation = reply.get(timeout=timeout)
        except queue.Empty:
            # The queued worker might still be performing the remote POST.
            # Never reissue it automatically.
            operation = store.get_remote(
                operation["operationId"], profile_id=profile_id,
                account_id=account_id, run_id=run_id,
            )
        return {"operation": operation, "reused": False}

    def _bind_pix_on_owner_thread(self, *, profile_id, account_id, run_id,
                                  operation_id, page):
        """Get secrets only on the owning Windows worker; never into logs."""
        from nexora_accounts.pix_operations import PixOperationStore, pix_owner_from_details
        from nexora_accounts.pix_browser_bind import bind_in_page
        from nexora_accounts.pix_key_store import PixKeyStore, normalize_key
        store = PixOperationStore()
        try:
            op = store.get_remote(operation_id, profile_id=profile_id,
                                  account_id=account_id, run_id=run_id)
            if op["status"] != "queued":
                return op
            details = LocalProfileStore().public_detail(profile_id)
            if (details.get("accountId") != account_id or
                details.get("lastSessionId") != run_id):
                return store.finish_remote(operation_id, profile_id=profile_id,
                    account_id=account_id, run_id=run_id,
                    result={"state": "failed", "stage": "client"})
            found = next((k for k in PixKeyStore().list().get("keys", ())
                          if k.get("id") == op["keyId"] and
                          k.get("type") == op["keyType"] and
                          k.get("updatedAt") == op.get("keyUpdatedAt")), None)
            if found is None:
                return store.finish_remote(operation_id, profile_id=profile_id,
                    account_id=account_id, run_id=run_id,
                    result={"state": "failed", "stage": "client"})
            _, key = normalize_key(found["value"], op["keyType"])
            # Read the same saved owner pair used by begin_remote. No
            # duplicate registration, confirmation, or login-password reuse.
            name, cpf = pix_owner_from_details(details)
            pin = LocalProfileStore().read_withdraw_pin(profile_id)
            result = bind_in_page(page, name=name,
                cpf=cpf, pin=pin, key=key, kind=op["keyType"])
        except Exception:
            result = {"state": "uncertain", "stage": "browser"}
        return store.finish_remote(operation_id, profile_id=profile_id,
            account_id=account_id, run_id=run_id, result=result)

    def _process_browser_request(self, profile_id: int, account_id: str,
                                 request_queue, page, *, run_id=None):
        """Consume only the exact account's request on its Playwright thread."""
        try:
            job = request_queue.get_nowait()
        except queue.Empty:
            return
        if job.get("accountId") != account_id or job.get("profileId") != profile_id:
            result = {"state": "account-mismatch", "message": "Sessão de outra conta bloqueada."}
        elif job.get("kind") == "pix-bind":
            if not run_id or job.get("runId") != run_id:
                result = {"state": "stale-session", "message": "Sessão Pix divergente."}
            else:
                result = self._bind_pix_on_owner_thread(
                    profile_id=profile_id, account_id=account_id, run_id=run_id,
                    operation_id=job.get("operationId"), page=page,
                )
        elif job.get("kind") == "verify-identity":
            if not run_id or job.get("runId") != run_id:
                result = {"state": "stale-session", "message": "Sessão divergente; identidade não atualizada."}
            else:
                result = self._verify_identity_on_owner_thread(
                    profile_id, account_id, run_id, page
                )
        else:
            result = self._probe_existing_page(page)
        try:
            job["result"].put_nowait(result)
        except queue.Full:
            pass

    def close(self, profile_id: int) -> dict:
        with self._lock:
            info = self._running.get(profile_id)
            if not info:
                return {"ok": True, "message": "Nenhum navegador ativo para esta conta."}
            info["stop"].set()
            self._results[profile_id] = {
                "state": "closing", "message": "Fechando navegador desta conta."
            }
        return {"ok": True, "message": "Fechamento solicitado para esta conta."}

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
        from pathlib import Path
        if (
            any(event.get("event") == "session.ready"
                for event in profile.get("history", []))
            and not Path(profile.get("profilePath") or "").is_dir()
        ):
            raise ValueError(
                "A pasta original desta conta não foi encontrada. "
                "Abra os detalhes e verifique a recuperação antes de reabrir."
            )
        url = str(profile.get("startUrl") or "").strip()
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("URL salva na conta é inválida.")
        module, tab = self._controller(gui, profile["origin"])
        instance_idx = next(self._instance_numbers)
        run_id = str(uuid.uuid4())
        stop_event = threading.Event()
        request_queue = queue.Queue(maxsize=1)
        with self._lock:
            if profile_id in self._running:
                raise AccountBusyError(
                    f"{profile['label']} já está aberta. Feche a janela antes de abrir novamente."
                )
            self._running[profile_id] = {"stop": stop_event, "requests": request_queue}
            self._results[profile_id] = {
                "state": "starting", "message": "Inicializando a sessão da conta."
            }

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
                LocalProfileStore().record_session(
                    profile_id, "starting", run_id, instance_idx
                )
            except Exception:
                pass
            def fail(message):
                log(message)
                self._set_state(profile_id, "error", message)
                try:
                    LocalProfileStore().record_session(profile_id, "error", run_id)
                except Exception:
                    pass
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
                    fail(
                        f"[Contas] {profile['label']}: modo de conexão original "
                        "indisponível ou legado; não será atribuído outro endereço automaticamente."
                    )
                    return
                if connection_mode == "managed" and not assigned_id:
                    fail(f"[Contas] {profile['label']}: vínculo da proxy original ausente; abertura cancelada.")
                    return
                if connection_mode == "direct" and assigned_id:
                    fail(f"[Contas] {profile['label']}: vínculo inesperado de proxy; abertura cancelada.")
                    return
                if assigned_id:
                    # A reopened account must NEVER use round-robin fallback.
                    # Deleted/missing bound proxy means a hard failure.
                    selected = next((p for p in listing if p["id"] == assigned_id), None)
                    if selected is None:
                        fail(f"[Contas] {profile['label']}: proxy original não disponível; abertura bloqueada.")
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
                            fail(f"[Contas] {profile['label']}: proxy original inválida ou indisponível ({result.get('status', 'erro')}).")
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
                    "run_id": run_id,
                    "browser_id": instance_idx,
                    "on_browser_command_tick": lambda page: self._process_browser_request(
                        profile_id, str(profile["accountId"]), request_queue, page
                    ),
                    "on_browser_ready": lambda: (
                        self._set_state(profile_id, "ready", "Chromium abriu a sessão desta conta."),
                        LocalProfileStore().record_session(profile_id, "ready", run_id),
                    ),
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
                fail(
                    f"[Contas] {profile['label']}: não foi possível iniciar "
                    f"({type(exc).__name__})."
                )
            finally:
                with self._lock:
                    if self._results.get(profile_id, {}).get("state") == "starting":
                        self._results[profile_id] = {
                            "state": "error",
                            "message": "O navegador não confirmou a abertura. Consulte os logs."
                        }
                    elif self._results.get(profile_id, {}).get("state") == "ready":
                        self._results[profile_id] = {
                            "state": "closed",
                            "message": "A sessão da conta foi encerrada."
                        }
                    self._running.pop(profile_id, None)
                if self.state(profile_id)["state"] == "closed":
                    try:
                        LocalProfileStore().record_session(profile_id, "closed", run_id)
                    except Exception:
                        pass

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
