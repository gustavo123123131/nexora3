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
        return ("pending", "Resposta recebida, mas sem confirmação estruturada verificável.")
    code = payload.get("code")
    success = payload.get("success")
    raw_status = payload.get("status")
    raw_message = payload.get("message", payload.get("msg", ""))
    status_text = str(raw_status).strip().lower() if isinstance(raw_status, str) else ""
    msg = str(raw_message).strip().lower() if isinstance(raw_message, str) else ""
    positive = msg in ("success", "successful", "registro realizado", "cadastro realizado",
                       "cadastro concluído", "registration successful", "registro exitoso")
    if success is True or (code == 0 and status_text == "success") or (code == 0 and positive):
        return ("created", "Confirmação explícita de sucesso na resposta do cadastro.")
    if success is False or (code is not None and isinstance(code, int) and code != 0):
        return ("error", "O site retornou rejeição ou código de erro no cadastro.")
    if status_text in ("error", "failed", "failure"):
        return ("error", "O site indicou falha no cadastro.")
    return ("pending", "Resposta do cadastro recebida, mas sem sinal inequívoco de sucesso ou falha.")


class RegistrationOutcomeObserver:
    def __init__(self, page, log, profile_id):
        self.page = page
        self.log = log
        self.profile_id = profile_id
        self.responses = []
        self._active = False

    def _on_response(self, response):
        try:
            route = urlsplit(response.url).path.rstrip("/").lower()
            if route == KNOWN_REGISTER_PATH and response.request.method.upper() == "POST":
                self.responses.append(response)
        except Exception:
            pass

    def start(self):
        self.page.on("response", self._on_response)
        self._active = True
        self.log(f"[#{self.profile_id}] [Registro] Observando POST {KNOWN_REGISTER_PATH}.")

    def close(self):
        if self._active:
            try:
                self.page.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._active = False

    def finish(self, stop_event, *, wait_seconds=12):
        """Wait for the response after click, then return a verified outcome."""
        try:
            deadline = time.monotonic() + max(0, wait_seconds)
            while not self.responses and time.monotonic() < deadline and not stop_event.is_set():
                self.page.wait_for_timeout(min(400, max(1, int((deadline-time.monotonic())*1000))))
            if stop_event.is_set():
                return ("pending", "Monitoramento interrompido; resultado do cadastro não confirmado.")
            if not self.responses:
                return ("pending", "Sem resposta observável de POST cadastro após 12 s. Verifique o resultado no site.")
            response = self.responses[-1]
            try:
                code = int(response.status)
                payload = response.json()
            except Exception:
                code = int(response.status)
                payload = None
            status, reason = classify_registration_reply(code, payload)
            self.log(f"[#{self.profile_id}] [Registro] Resposta da rota observada: HTTP {code}.")
            return status, reason
        except Exception as exc:
            return ("pending", f"Não foi possível concluir a verificação ({type(exc).__name__}).")
        finally:
            self.close()
