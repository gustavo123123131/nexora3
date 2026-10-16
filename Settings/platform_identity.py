"""Passive collection of an authenticated user's remote identity.

No production site adapter is configured. A reviewed origin-specific adapter
is required before any candidate may be accepted or persisted. This module
never sends HTTP requests, navigates, clicks, or logs personal values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
        self.contract = contract if (
            contract is not None and self.origin
            and site_origin(getattr(contract, "origin", None)) == self.origin
            and callable(getattr(contract, "validate_identity", None))
        ) else None
        self._responses = []
        self._seen = set()
        self._processed = 0
        self._active = False
        self._context_listening = False

    def _on_response(self, response):
        try:
            if site_origin(response.url) != self.origin:
                return
            # A Chromium context can contain multiple tabs on the SAME
            # origin. Ignore responses attributed to another tab, even if
            # Playwright forwards them to a context-level listener.
            try:
                frame = response.request.frame
            except Exception:
                # Service worker / unframed requests cannot be attributed
                # unambiguously to this profile's page.
                return
            if frame is None or frame.page is not self.page:
                return
            # Store response handles, never bodies, headers, cookies or tokens.
            key = id(response)
            if key not in self._seen and len(self._responses) < 64:
                self._seen.add(key)
                self._responses.append(response)
        except Exception:
            pass

    def start(self):
        if self._active:
            return
        if not self.origin:
            return
        self.page.on("response", self._on_response)
        self._active = True
        context = getattr(self.page, "context", None)
        if context is not None and hasattr(context, "on"):
            context.on("response", self._on_response)
            self._context_listening = True

    def close(self):
        if self._active:
            try:
                self.page.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._active = False
        if self._context_listening:
            try:
                self.page.context.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._context_listening = False
        self._responses.clear()
        self._seen.clear()

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
            self.log("[Registro] ID da plataforma: não identificado (contrato de identidade ausente).")
            return None
        if stop_event is not None and stop_event.is_set():
            return None
        # Give an independently verified registration response first priority.
        # Never trust a generic status/uid just because Registrar was clicked.
        if (getattr(registration_verdict, "status", None) == "created"
            and getattr(registration_verdict, "confirmation_source", None)):
            result = self._registration_identity(registration_verdict)
            if result:
                return result
        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            result = self._identity_responses()
            if result:
                return result
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
        for check in (self._identity_storage, self._identity_profile):
            result = check()
            if result:
                return result
        self.log("[Registro] ID da plataforma: não identificado nas fontes validadas.")
        return None
