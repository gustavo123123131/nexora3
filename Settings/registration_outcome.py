"""Conservative, session-scoped observation of registration responses.

No production provider contract has been verified. The default path NEVER
interprets generic success/uid/code fields as proof of registration or identity.
A provider-specific, reviewed contract may be supplied in a later change.
The existing DOM filling and submission flow is deliberately untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlsplit


KNOWN_REGISTER_PATH = "/hall/api/member/register"


def _origin(value):
    try:
        p = urlsplit(str(value or ""))
        if p.scheme.lower() not in ("https", "http") or not p.hostname:
            return ""
        host = p.hostname.lower()
        port = p.port
        standard = 443 if p.scheme.lower() == "https" else 80
        return p.scheme.lower() + "://" + host + (
            ":" + str(port) if port is not None and port != standard else ""
        )
    except (ValueError, TypeError):
        return ""


@dataclass(frozen=True)
class RegistrationVerdict:
    status: str
    reason: str
    confirmation_source: str | None = None
    platform_user_id: str | None = None
    platform_id_source: str | None = None


def _unconfirmed():
    return RegistrationVerdict(
        "unconfirmed",
        "Resposta observada, mas não existe contrato comprovado para confirmar cadastro ou identificar usuário.",
    )


def interpret_registration_reply(http_status, payload, *, origin="", contract=None):
    """Interpret only through an explicitly verified per-origin contract.

    Contract API reserved for a future reviewed, site-specific adapter:
    .origin is the exact site origin; .interpret(status, payload) returns
    RegistrationVerdict. No production adapters are bundled or enabled.
    """
    if contract is None or not origin or _origin(getattr(contract, "origin", "")) != _origin(origin):
        return _unconfirmed()
    try:
        verdict = contract.interpret(int(http_status), payload)
    except Exception:
        return _unconfirmed()
    if not isinstance(verdict, RegistrationVerdict) or verdict.status not in (
        "unconfirmed", "verification_required", "created", "rejected",
    ):
        return _unconfirmed()
    if verdict.status == "created" and not verdict.confirmation_source:
        return _unconfirmed()
    if verdict.status != "created" and (
        verdict.platform_user_id is not None or verdict.platform_id_source is not None
    ):
        return _unconfirmed()
    if verdict.platform_user_id is not None and (
        not isinstance(verdict.platform_user_id, str)
        or not verdict.platform_user_id
        or len(verdict.platform_user_id) > 128
        or not verdict.platform_user_id.isprintable()
        or any(ch.isspace() for ch in verdict.platform_user_id)
        or not verdict.platform_id_source
    ):
        return _unconfirmed()
    # Contract diagnostics must not echo the server's message or payload.
    safe = {
        "verification_required": "O provedor solicitou verificação adicional comprovada.",
        "created": "Cadastro confirmado pelo contrato verificado deste provedor.",
        "rejected": "Cadastro recusado conforme o contrato verificado deste provedor.",
        "unconfirmed": "Resposta sem conclusão verificável.",
    }
    return RegistrationVerdict(
        verdict.status, safe[verdict.status],
        verdict.confirmation_source if verdict.status == "created" else None,
        verdict.platform_user_id if verdict.status == "created" else None,
        verdict.platform_id_source if verdict.status == "created" else None,
    )


def classify_registration_reply(http_status, payload, *, origin="", contract=None):
    """Legacy tuple API; generic HTTP 200/success/status/code never means created."""
    verdict = interpret_registration_reply(http_status, payload, origin=origin, contract=contract)
    return verdict.status, verdict.reason


class RegistrationOutcomeObserver:
    def __init__(self, page, log, profile_id, *, expected_origin=None, contract=None):
        self.page = page
        self.log = log
        self.profile_id = profile_id
        self.origin = _origin(expected_origin if expected_origin is not None else getattr(page, "url", ""))
        self.contract = contract
        self.responses = []
        self.other_registration_posts = set()
        self._active = False
        self._context_registered = False
        self._seen = set()
        self._next_response = 0
        self.verdict = _unconfirmed()

    def _on_response(self, response):
        try:
            if response.request.method.upper() != "POST":
                return
            # A context may have multiple tabs on the same domain. Only
            # attribute framed responses to the page that registered.
            frame = getattr(response.request, "frame", None)
            if frame is not None:
                source_page = getattr(frame, "page", None)
                if source_page is not None and source_page is not self.page:
                    return
            parsed = urlsplit(response.url)
            if not self.origin or _origin(response.url) != self.origin:
                return
            path = parsed.path.rstrip("/").lower()
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
        context = getattr(self.page, "context", None)
        if context is not None and hasattr(context, "on"):
            context.on("response", self._on_response)
            self._context_registered = True
        self.log(f"[#{self.profile_id}] [Registro] Monitorando respostas da origem da conta.")

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

    def finish(self, stop_event, *, wait_seconds=15, verification_wait_seconds=120):
        """Follow multiple POST responses; never resend a registration request.

        Without a proved contract we time out as unconfirmed, retaining the
        original account and all browser data. A challenge is only recognized
        by a verified site adapter. The extra wait gives the user time to
        complete the site's official challenge.
        """
        try:
            if not self.origin:
                return "unconfirmed", "Origem da plataforma não identificada; nenhuma resposta foi atribuída."
            deadline = time.monotonic() + max(0, wait_seconds)
            challenged = False
            saw_response = False
            while not stop_event.is_set():
                while self._next_response < len(self.responses):
                    response = self.responses[self._next_response]
                    self._next_response += 1
                    saw_response = True
                    try:
                        finished = getattr(response, "finished", None)
                        if callable(finished) and finished():
                            raise ValueError("network_response_incomplete")
                        status_code = int(response.status)
                        payload = response.json()
                    except Exception:
                        status_code, payload = 0, None
                    verdict = interpret_registration_reply(
                        status_code, payload, origin=self.origin, contract=self.contract
                    )
                    # Never print body, decoded fields, messages or credentials.
                    self.log(
                        f"[#{self.profile_id}] [Registro] Resposta de cadastro #{self._next_response}: "
                        f"HTTP {status_code}; resultado {verdict.status}."
                    )
                    if verdict.status == "verification_required":
                        challenged = True
                        self.verdict = verdict
                        deadline = max(deadline, time.monotonic() + max(0, verification_wait_seconds))
                    elif verdict.status in ("created", "rejected"):
                        self.verdict = verdict
                        return verdict.status, verdict.reason
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.page.wait_for_timeout(max(1, min(400, int(remaining * 1000))))
            if stop_event.is_set():
                return "unconfirmed", "Monitoramento interrompido antes de confirmar o cadastro."
            if challenged:
                return "verification_required", "Verificação adicional pendente ou resultado final ainda não recebido."
            if saw_response:
                return "unconfirmed", "Resposta recebida, mas o resultado final do cadastro não foi comprovado."
            if self.other_registration_posts:
                paths = ", ".join(sorted(self.other_registration_posts)[:3])
                self.log(f"[#{self.profile_id}] [Registro] Outras rotas POST observadas: {paths}.")
                return "unconfirmed", "O site utilizou rota de cadastro ainda não documentada."
            return "unconfirmed", "Nenhuma resposta da rota comprovada observada no período."
        except Exception as exc:
            return "unconfirmed", "Monitoramento não concluído (" + type(exc).__name__ + ")."
        finally:
            self.close()
