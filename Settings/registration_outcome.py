"""Conservative registration outcome observer for the observed browser route.

The existing form is unchanged. An HTTP 200 or click alone is NEVER proof
of account creation. Only explicit response fields on the observed
/hall/api/member/register endpoint can set status to created.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit


KNOWN_REGISTER_PATH = "/hall/api/member/register"


def classify_registration_reply(http_status, payload):
    """Return (status, diagnostic). Never expose user data or server messages."""
    if http_status >= 400:
        return ("error", f"Servidor recusou envio: HTTP {http_status}.")
    if not isinstance(payload, dict):
        return ("unconfirmed", "Resposta recebida, mas não contém confirmação estruturada de cadastro.")
    code = payload.get("code")
    success = payload.get("success")
    raw_status = payload.get("status")
    raw_message = payload.get("message", payload.get("msg", ""))
    status_text = str(raw_status).strip().lower() if isinstance(raw_status, str) else ""
    msg = str(raw_message).strip().lower() if isinstance(raw_message, str) else ""
    positive = msg in (
        "success", "successful", "ok", "registro realizado",
        "cadastro realizado", "cadastro concluído", "cadastro realizado com sucesso",
        "registration successful", "registered successfully", "register success",
        "registro exitoso", "注册成功",
    )
    nested = payload.get("data")
    nested_confirm = isinstance(nested, dict) and nested.get("success") is True
    # Only clear semantic confirmation is sufficient. HTTP 200/code 0 alone
    # is NOT proof of an account being created.
    if success is True or nested_confirm or (
        status_text in ("success", "created", "registered") and code in (0, 200, None)
    ) or (code in (0, 200) and positive):
        return ("created", "Confirmação explícita de sucesso na resposta do cadastro.")
    if success is False or (code is not None and isinstance(code, int) and code != 0):
        return ("error", "O site retornou rejeição ou código de erro no cadastro.")
    if status_text in ("error", "failed", "failure"):
        return ("error", "O site indicou falha no cadastro.")
    return ("unconfirmed", "Resposta recebida sem indicador inequívoco de cadastro concluído ou rejeitado.")


class RegistrationOutcomeObserver:
    def __init__(self, page, log, profile_id):
        self.page = page
        self.log = log
        self.profile_id = profile_id
        self.responses = []
        self.other_registration_posts = set()
        self._active = False
        self._context_registered = False
        self._seen = set()

    def _on_response(self, response):
        try:
            if response.request.method.upper() != "POST":
                return
            path = urlsplit(response.url).path.rstrip("/").lower()
            # Never record payloads, query strings, tokens or credentials.
            if path == KNOWN_REGISTER_PATH:
                key = id(response)
                if key not in self._seen:
                    self._seen.add(key)
                    self.responses.append(response)
            elif "register" in path or "signup" in path:
                self.other_registration_posts.add(path[:100])
        except Exception:
            pass

    def start(self):
        self.page.on("response", self._on_response)
        self._active = True
        # A submit may trigger navigation or a popup. The browser context
        # hears the response without guessing another endpoint contract.
        context = getattr(self.page, "context", None)
        if context is not None and hasattr(context, "on"):
            context.on("response", self._on_response)
            self._context_registered = True
        self.log(f"[#{self.profile_id}] [Registro] Observando resposta POST de cadastro.")

    def close(self):
        if self._active:
            try:
                self.page.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._active = False
        if self._context_registered:
            try:
                self.page.context.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._context_registered = False

    def finish(self, stop_event, *, wait_seconds=15):
        """Return a verified outcome and a specific, privacy-safe explanation."""
        try:
            deadline = time.monotonic() + max(0, wait_seconds)
            while not self.responses and time.monotonic() < deadline and not stop_event.is_set():
                self.page.wait_for_timeout(
                    min(400, max(1, int((deadline - time.monotonic()) * 1000)))
                )
            if stop_event.is_set():
                return ("unconfirmed", "Monitoramento interrompido antes de confirmar o cadastro.")
            if not self.responses:
                if self.other_registration_posts:
                    paths = ", ".join(sorted(self.other_registration_posts)[:3])
                    self.log(f"[#{self.profile_id}] [Registro] Outras rotas POST observadas: {paths}.")
                    return ("unconfirmed",
                            "O site utilizou uma rota de cadastro não identificada; "
                            "resultado sem confirmação segura. Veja logs de registro.")
                return ("unconfirmed",
                        "Nenhuma resposta POST da rota de cadastro observada após 15 segundos.")
            response = self.responses[-1]
            status_code = int(response.status)
            try:
                payload = response.json()
            except Exception:
                payload = None
            status, reason = classify_registration_reply(status_code, payload)
            if isinstance(payload, dict):
                keys = sorted(
                    key for key in payload.keys()
                    if key in ("code", "success", "status", "message", "msg", "data", "error")
                )
                self.log(
                    f"[#{self.profile_id}] [Registro] HTTP {status_code}; "
                    f"campos de resultado: {', '.join(keys) or 'nenhum'}."
                )
            else:
                self.log(
                    f"[#{self.profile_id}] [Registro] HTTP {status_code}; "
                    "resposta sem objeto JSON verificável."
                )
            return status, reason
        except Exception as exc:
            return ("unconfirmed",
                    "Verificação da resposta não concluída (" + type(exc).__name__ + ").")
        finally:
            self.close()
