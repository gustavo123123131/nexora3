"""Passive collection of an authenticated user's remote identity.

No production site adapter is configured. A reviewed origin-specific adapter
is required before any candidate may be accepted or persisted. This module
never sends HTTP requests, navigates, clicks, or logs personal values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from urllib.parse import urlsplit


def site_origin(url):
    try:
        p = urlsplit(str(url or ""))
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return ""
        normal_port = 443 if p.scheme.lower() == "https" else 80
        return p.scheme.lower() + "://" + p.hostname.lower() + (
            ":" + str(p.port) if p.port not in (None, normal_port) else ""
        )
    except (TypeError, ValueError):
        return ""


def _text(value, limit=128):
    return (isinstance(value, str) and 1 <= len(value) <= limit
            and value.isprintable() and not any(c.isspace() for c in value))


# Both exact hosts have been used as registration origins in the project.
# Do not admit look-alike subdomains or arbitrary redirect destinations.
FIVEGBET_SITE_ORIGINS = frozenset({
    "https://5gbet.com", "https://www.5gbet.com",
})


class FiveGBetIdentityContract:
    """Observed 5gbet header location; NO authenticated response contract yet.

    The operator verified that x-object-id.uid matches the official Profile
    ID in one real browser session. Its semantics are grounded, but an
    outgoing request header is not evidence the remote server authenticated
    that request. Therefore the built-in adapter collects candidates only.

    An independently reviewed, provider-specific validator may implement
    verify_header_identity(uid, response, body, account), returning True only
    when that response contains independently proven authenticated ownership
    of the very same UID. Generic HTTP 200, success:true or err_code:0 alone
    MUST NOT be considered such proof.
    """
    origin = "https://www.5gbet.com"
    identity_header_requests = frozenset({
        ("GET", "https://cdnapiif.ppa5g.net", "/hall/api/message/list/all"),
    })
    identity_response_routes = ()
    identity_storage_fields = ()
    identity_profile_fields = ()

    def validate_identity(self, value, source, account):
        # No production identity *confirmation* schema has been demonstrated.
        return False


def provider_identity_contract(origin):
    """Enable the audited header location for either exact known site host."""
    if site_origin(origin) in FIVEGBET_SITE_ORIGINS:
        return FiveGBetIdentityContract()
    return None


def parse_x_object_id_uid(raw):
    """Extract ONLY the verified x-object-id JSON uid as a positive integer."""
    if not isinstance(raw, str) or not (2 <= len(raw) <= 4096):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    uid = obj.get("uid")
    # bool is a subclass of int in Python: explicitly reject it.
    if type(uid) is not int or uid <= 0 or uid > (2**53 - 1):
        return None
    return str(uid)


@dataclass(frozen=True)
class VerifiedPlatformIdentity:
    user_id: str
    source: str
    # Filled only by PlatformIdentityObserver after a source-specific adapter
    # checked the authenticated account. Defaults preserve the old return API.
    profile_id: int | None = field(default=None, compare=False)
    account_id: str | None = field(default=None, compare=False)
    run_id: str | None = field(default=None, compare=False)
    platform_origin: str | None = field(default=None, compare=False)


class PlatformIdentityObserver:
    """Bounded response observation restricted to one Chromium context/origin.

    The optional reviewed adapter must define:
      origin: exact site origin;
      validate_identity(user_id, source, account): bool, verifying both ID
          semantics/format and correspondence with this authenticated account;
      identity_response_routes: {(method, exact_path), ...};
      identity_from_response(url, status, payload, account): (id, source) | None.

    Optionally, the adapter may define audited identity_storage_fields with
    {area, key, path, source} or identity_profile_fields with
    {selector, attribute, source}. No heuristic field names, arbitrary
    storage scanning, speculative endpoints or generic DOM selectors are used.
    """

    def __init__(self, page, log, account, *, expected_origin, contract=None):
        self.page = page
        self.log = log
        self.account = {
            "profileId": account.get("id"),
            "accountId": account.get("accountId"),
            "runId": account.get("runId"),
            "username": account.get("username"),
        }
        self.origin = site_origin(expected_origin)
        self._official_site = self.origin in FIVEGBET_SITE_ORIGINS
        self._last_site_origin = self.origin
        self._diagnosed = set()
        self._request_route_seen = False
        self._header_present = False
        self._header_invalid = False
        if contract is None:
            contract = provider_identity_contract(self.origin)
        self.contract = contract if (
            contract is not None and self.origin
            and (
                site_origin(getattr(contract, "origin", None)) == self.origin
                or (self._official_site
                    and isinstance(contract, FiveGBetIdentityContract)
                    and site_origin(contract.origin) in FIVEGBET_SITE_ORIGINS)
            )
            and callable(getattr(contract, "validate_identity", None))
        ) else None
        # Only the exact audited CDN host/path may add another origin.
        routes = getattr(self.contract, "identity_header_requests", ())
        self._header_routes = frozenset(
            (method, api_origin, path)
            for method, api_origin, path in routes
            if (method, api_origin, path) ==
            ("GET", "https://cdnapiif.ppa5g.net", "/hall/api/message/list/all")
            and self._official_site
        ) if self.contract is not None else frozenset()
        self._response_origins = {self.origin}
        if self._header_routes:
            self._response_origins.update(FIVEGBET_SITE_ORIGINS)
            self._response_origins.add("https://cdnapiif.ppa5g.net")
        self._header_requests = {}
        self._pending_header_requests = {}
        self._ready_header_ids = set()
        self._scanned_request_ids = set()
        self._scanned_request_order = []
        self._header_seen_uid = None
        self._header_conflict = False
        self._clicked_at = None
        self._responses = []
        self._seen = set()
        self._processed = 0
        self._active = False
        self._context_listening = False
        self._request_listening = False
        self._context_request_listening = False
        self._navigation_listening = False
        self._header_responses_checked = set()

    def _log_once(self, key, message):
        if key not in self._diagnosed:
            self._diagnosed.add(key)
            try:
                self.log("[Registro] [ID] " + message)
            except Exception:
                pass

    def note_navigation(self, url):
        """Diagnose the final host and any allowed apex/www redirect."""
        new_origin = site_origin(url)
        if not new_origin or new_origin == self._last_site_origin:
            return
        self._last_site_origin = new_origin
        if self._official_site:
            self._official_site = new_origin in FIVEGBET_SITE_ORIGINS
            label = ("oficial" if self._official_site else "fora da lista permitida")
            self._log_once(
                "navigation-" + label,
                "Origem após navegação: " + label
                + (f" ({new_origin})" if self._official_site else "")
                + "; adaptador " + ("ativo." if self._official_site else "suspenso."),
            )
        elif new_origin in FIVEGBET_SITE_ORIGINS:
            # An untrusted initial domain must not automatically inherit
            # 5gbet identity or overwrite its recorded startUrl.
            self._log_once(
                "unexpected-redirect",
                "Redirecionamento para domínio oficial observado, mas "
                "origem inicial não autorizada; não capturando ID.",
            )

    def _on_frame_navigated(self, frame):
        try:
            if frame is self.page.main_frame:
                self.note_navigation(frame.url)
        except Exception:
            return

    def _collect_request(self, request, *, page_scoped):
        if not self._header_routes or not self._official_site:
            return
        try:
            parsed = urlsplit(request.url)
            route = (
                request.method.upper(),
                site_origin(request.url),
                parsed.path.rstrip("/").lower(),
            )
            if route not in self._header_routes:
                return
            self._request_route_seen = True
            self._log_once("request-route", "Requisição permitida da API observada.")
            if not page_scoped:
                try:
                    frame = request.frame
                    if frame is None or frame.page is not self.page:
                        return
                except Exception:
                    return
            # The same request is emitted by page and context listeners.
            # Deduplicate BEFORE reading all_headers() (including invalid
            # or missing x-object-id) to avoid duplicate sensitive reads.
            key = id(request)
            # Hold a bounded strong reference to each recently seen Request.
            # Numeric id() values can be reused after GC in long-lived runs.
            if request in self._scanned_request_ids:
                return
            if len(self._scanned_request_order) >= 128:
                old = self._scanned_request_order.pop(0)
                self._scanned_request_ids.discard(old)
            self._scanned_request_ids.add(request)
            self._scanned_request_order.append(request)
            # Do NOT call all_headers() inside Playwright's request event:
            # the Chromium extra-info packet may arrive only afterwards and
            # block a synchronous event handler. Queue the bound handle
            # without copying other headers or tokens.
            if len(self._pending_header_requests) >= 64:
                oldest = next(iter(self._pending_header_requests))
                self._pending_header_requests.pop(oldest, None)
                self._ready_header_ids.discard(oldest)
            self._pending_header_requests[key] = (request, time.monotonic())
        except Exception:
            return

    def inspect_pending_headers(self):
        """Read only completed allowlisted requests, outside event callbacks."""
        for key in tuple(self._ready_header_ids):
            self._ready_header_ids.discard(key)
            queued = self._pending_header_requests.pop(key, None)
            if queued is None:
                continue
            request, timestamp = queued
            try:
                # No passwords, tokens, cookies or JWT are kept or logged.
                headers = request.all_headers()
                if not isinstance(headers, dict):
                    continue
                raw = next(
                    (v for k, v in headers.items()
                     if isinstance(k, str) and k.lower() == "x-object-id"),
                    None,
                )
                if raw is not None:
                    self._header_present = True
                else:
                    self._log_once(
                        "missing-header", "Requisição permitida sem cabeçalho x-object-id.",
                    )
                uid = parse_x_object_id_uid(raw)
                if uid is None:
                    if raw is not None:
                        self._header_invalid = True
                        self._log_once(
                            "invalid-header", "x-object-id presente, uid inválido.",
                        )
                    continue
                self._log_once(
                    "candidate",
                    "x-object-id.uid observado (valor protegido); aguardando verificação.",
                )
                if self._header_seen_uid is not None and self._header_seen_uid != uid:
                    self._header_conflict = True
                    self._log_once(
                        "conflict", "IDs de cabeçalhos conflitantes; nenhum ID será salvo.",
                    )
                else:
                    self._header_seen_uid = uid
                if len(self._header_requests) >= 32:
                    self._header_requests.pop(next(iter(self._header_requests)), None)
                self._header_requests[key] = (request, uid, timestamp)
            except Exception:
                self._log_once(
                    "header-read-error",
                    "Cabeçalho da requisição não disponível após a resposta.",
                )

    def _on_page_request(self, request):
        self._collect_request(request, page_scoped=True)

    def _on_context_request(self, request):
        self._collect_request(request, page_scoped=False)

    def after_registration_click(self):
        """Called AFTER the existing Criada-by-click write, not before it."""
        self._clicked_at = time.monotonic()

    def _collect_response(self, response, *, page_scoped):
        try:
            if site_origin(response.url) not in self._response_origins:
                return
            if not page_scoped:
                # A Chromium context may contain several tabs on the same
                # origin. For context events require an exact frame->page
                # match; unframed service workers are ambiguous and ignored.
                try:
                    frame = response.request.frame
                    if frame is None or frame.page is not self.page:
                        return
                except Exception:
                    return
            # A response indicates headers can now be inspected without
            # blocking Playwright's request event.
            try:
                request_key = id(response.request)
                if request_key in self._pending_header_requests:
                    self._ready_header_ids.add(request_key)
            except Exception:
                pass
            # Keep only bounded response handles from the owning page; never
            # retain payloads, cookies, request headers or token-bearing URLs.
            key = id(response)
            if key not in self._seen:
                if len(self._responses) >= 64:
                    oldest = self._responses.pop(0)
                    self._seen.discard(id(oldest))
                    self._header_responses_checked.discard(id(oldest))
                    if self._processed:
                        self._processed -= 1
                self._seen.add(key)
                self._responses.append(response)
        except Exception:
            pass

    def _on_page_response(self, response):
        # Playwright page.on('response') is already scoped to this page.
        self._collect_response(response, page_scoped=True)

    def _on_response(self, response):
        self._collect_response(response, page_scoped=False)

    def start(self):
        if self._active:
            return
        if not self.origin:
            self._log_once("no-origin", "Origem inicial inválida; adaptador não ativado.")
            return
        self._log_once(
            "activation",
            "Adaptador " + (
                "5gbet ativo; origem inicial " + self.origin + "."
                if self._header_routes else
                "não ativado para a origem inicial; contrato de identidade ausente."
            ),
        )
        self.page.on("response", self._on_page_response)
        self._active = True
        try:
            self.page.on("framenavigated", self._on_frame_navigated)
            self._navigation_listening = True
        except Exception:
            pass
        if self._header_routes:
            self.page.on("request", self._on_page_request)
            self._request_listening = True
        context = getattr(self.page, "context", None)
        if context is not None and hasattr(context, "on"):
            context.on("response", self._on_response)
            self._context_listening = True
            if self._header_routes:
                context.on("request", self._on_context_request)
                self._context_request_listening = True

    def close(self):
        if self._active and self._header_routes:
            self.inspect_pending_headers()
            if not self._request_route_seen:
                self._log_once("route-absent", "Nenhuma requisição permitida da API observada nesta sessão.")
            elif self._header_seen_uid is None:
                self._log_once("uid-absent", "Requisição observada, mas nenhum uid válido capturado.")
            else:
                self._log_once(
                    "candidate-final", "ID observado, não verificado; "
                    "falta evidência independente de autenticação.",
                )
        if self._navigation_listening:
            try:
                self.page.remove_listener("framenavigated", self._on_frame_navigated)
            except Exception:
                pass
            self._navigation_listening = False
        if self._active:
            try:
                self.page.remove_listener("response", self._on_page_response)
            except Exception:
                pass
            self._active = False
        if self._context_listening:
            try:
                self.page.context.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._context_listening = False
        if self._request_listening:
            try:
                self.page.remove_listener("request", self._on_page_request)
            except Exception:
                pass
            self._request_listening = False
        if self._context_request_listening:
            try:
                self.page.context.remove_listener("request", self._on_context_request)
            except Exception:
                pass
            self._context_request_listening = False
        self._responses.clear()
        self._seen.clear()
        self._header_requests.clear()
        self._pending_header_requests.clear()
        self._ready_header_ids.clear()
        self._scanned_request_ids.clear()
        self._scanned_request_order.clear()
        self._header_seen_uid = None
        self._header_conflict = False
        self._header_responses_checked.clear()

    def _accept(self, candidate):
        if not isinstance(candidate, (list, tuple)) or len(candidate) != 2:
            return None
        value, source = candidate
        if not _text(value) or not _text(source):
            return None
        if value in (self.account["username"], self.account["accountId"],
                     self.account["runId"]):
            return None
        try:
            if self.contract.validate_identity(
                value, source, dict(self.account)
            ) is True:
                return VerifiedPlatformIdentity(
                    value, source,
                    profile_id=self.account["profileId"],
                    account_id=self.account["accountId"],
                    run_id=self.account["runId"],
                    platform_origin=self.origin,
                )
        except Exception:
            pass
        return None

    def _registration_identity(self, verdict):
        if not getattr(verdict, "platform_user_id", None):
            return None
        return self._accept((
            verdict.platform_user_id, getattr(verdict, "platform_id_source", None)
        ))

    def _identity_responses(self):
        try:
            routes = {
                (str(method).upper(), path.rstrip("/").lower())
                for method, path in getattr(self.contract, "identity_response_routes", ())
                if isinstance(path, str) and path.startswith("/")
                and "?" not in path and "#" not in path
            }
        except (ValueError, TypeError):
            routes = set()
        parser = getattr(self.contract, "identity_from_response", None)
        if not routes or not callable(parser):
            return None
        pending = self._responses[self._processed:]
        for response in pending:
            try:
                path = urlsplit(response.url).path.rstrip("/").lower()
                if (response.request.method.upper(), path) not in routes:
                    continue
                finished = getattr(response, "finished", None)
                if callable(finished) and finished():
                    continue
                candidate = parser(
                    response.url, int(response.status), response.json(),
                    dict(self.account)
                )
                result = self._accept(candidate)
                if result:
                    return result
            except Exception:
                continue
        self._processed = len(self._responses)
        return None

    def _identity_header(self):
        """Require an independent provider-specific authenticated identity.

        The x-object-id uid from an OUTGOING request is a candidate, not proof
        of server acceptance. A non-2xx / err_code:41000 response is rejected;
        a generic HTTP 200 / err_code:0 is still insufficient. No new traffic.
        """
        if (self._clicked_at is None or not self._header_routes
            or self._header_conflict or not self._official_site):
            return None
        verifier = getattr(self.contract, "verify_header_identity", None)
        for response in self._responses:
            try:
                request = response.request
                detail = self._header_requests.get(id(request))
                if detail is None or detail[0] is not request:
                    continue
                _, uid, seen_at = detail
                if seen_at < self._clicked_at - 3.0:
                    continue
                response_key = id(response)
                if response_key in self._header_responses_checked:
                    continue
                status = int(response.status)
                if not 200 <= status < 300:
                    self._log_once(
                        "response-http-error",
                        "Resposta da API com HTTP não 2xx; cabeçalho não comprova login.",
                    )
                    self._header_responses_checked.add(response_key)
                    continue
                finished = getattr(response, "finished", None)
                if callable(finished) and finished():
                    continue
                body = response.json()
                if isinstance(body, dict) and (
                    "err_code" in body
                    and (type(body["err_code"]) is not int or body["err_code"] != 0)
                ):
                    self._log_once(
                        "response-api-error",
                        "Resposta da API contém erro; uid não verificado.",
                    )
                    self._header_responses_checked.add(response_key)
                    continue
                self._header_responses_checked.add(response_key)
                if not callable(verifier):
                    self._log_once(
                        "missing-verifier",
                        "ID observado; não salvo: falta contrato independente "
                        "de autenticação do provedor.",
                    )
                    continue
                if verifier(uid, response, body, dict(self.account)) is not True:
                    self._log_once(
                        "unverified-response",
                        "ID observado; resposta sem prova independente "
                        "da conta autenticada.",
                    )
                    continue
                accepted = self._accept((uid, "5gbet:x-object-id.uid:authenticated"))
                if accepted:
                    self._log_once(
                        "verified",
                        "ID verificado por fonte independente para a sessão.",
                    )
                    return accepted
            except Exception:
                continue
        return None

    def _identity_storage(self):
        for spec in getattr(self.contract, "identity_storage_fields", ()):
            try:
                if not isinstance(spec, dict) or not _text(spec.get("source")):
                    continue
                if spec.get("area") not in ("local", "session"):
                    continue
                key, path = spec.get("key"), spec.get("path")
                if (not _text(key, 256) or not isinstance(path, (list, tuple))
                    or len(path) > 8 or any(
                        not isinstance(k, str) or not k or k in
                        ("__proto__", "constructor", "prototype") for k in path
                    )):
                    continue
                if site_origin(self.page.url) != self.origin:
                    continue
                leaf = self.page.evaluate(
                    """s => {
                        const storage = s.area === 'local' ? localStorage : sessionStorage;
                        const raw = storage.getItem(s.key);
                        if (raw === null) return null;
                        let value;
                        try { value = JSON.parse(raw); } catch (_) { value = raw; }
                        for (const key of s.path) {
                            if (value === null || typeof value !== 'object'
                                || !Object.prototype.hasOwnProperty.call(value, key))
                                return null;
                            value = value[key];
                        }
                        return typeof value === 'string' ? value : null;
                    }""",
                    {"area": spec["area"], "key": key, "path": list(path)}
                )
                found = self._accept((leaf, spec["source"]))
                if found:
                    return found
            except Exception:
                continue
        return None

    def _identity_profile(self):
        for spec in getattr(self.contract, "identity_profile_fields", ()):
            try:
                if not isinstance(spec, dict) or not _text(spec.get("source")):
                    continue
                selector = spec.get("selector")
                if not isinstance(selector, str) or not selector or len(selector) > 250:
                    continue
                if site_origin(self.page.url) != self.origin:
                    continue
                locator = self.page.locator(selector)
                if locator.count() != 1:
                    continue
                attribute = spec.get("attribute", "text")
                value = (
                    locator.inner_text(timeout=750) if attribute == "text"
                    else locator.get_attribute(attribute, timeout=750)
                    if isinstance(attribute, str) and attribute.startswith("data-")
                    else None
                )
                found = self._accept((value, spec["source"]))
                if found:
                    return found
            except Exception:
                continue
        return None

    def identify(self, *, registration_verdict=None, stop_event=None, wait_seconds=10):
        """Read independently verified identity after the user's signup click.

        A trusted registration verdict is OPTIONAL: the existing GUI labels
        accounts Criada immediately after a click. That display status cannot
        serve as identity proof. A per-origin adapter must validate the actual
        authenticated identity and ownership from the selected source.
        """
        if self.contract is None:
            self._log_once("no-contract", "ID não identificado: contrato de identidade ausente.")
            return None
        if stop_event is not None and stop_event.is_set():
            return None
        self.inspect_pending_headers()
        # Give an independently verified registration response first priority.
        # Never trust a generic status/uid just because Registrar was clicked.
        if (getattr(registration_verdict, "status", None) == "created"
            and getattr(registration_verdict, "confirmation_source", None)):
            result = self._registration_identity(registration_verdict)
            if result:
                return result
        deadline = time.monotonic() + max(0, wait_seconds)
        pending_header = None
        while True:
            result = self._identity_responses()
            if result:
                return result
            result = self._identity_header()
            if result:
                pending_header = result
            if (time.monotonic() >= deadline or
                (stop_event is not None and stop_event.is_set())):
                break
            try:
                self.page.wait_for_timeout(
                    max(1, min(250, int((deadline - time.monotonic()) * 1000)))
                )
            except Exception:
                break
        if stop_event is not None and stop_event.is_set():
            return None
        # Check the whole observation window for conflicting UIDs. Never
        # persist the first acceptable UID if another API request in that
        # same session claims a different one moments later.
        if (pending_header is not None and not self._header_conflict
            and pending_header.user_id == self._header_seen_uid):
            return pending_header
        for check in (self._identity_storage, self._identity_profile):
            result = check()
            if result:
                return result
        if self._header_seen_uid is not None and self._clicked_at is not None:
            self._log_once(
                "candidate-pending", "ID observado, mas ainda não verificado "
                "por resposta de autenticação independente; ID não salvo.",
            )
        elif self._clicked_at is not None:
            self._log_once(
                "no-uid", "Ainda não foi encontrado uid autenticado nesta sessão; "
                "o monitor continuará ativo.",
            )
        return None
