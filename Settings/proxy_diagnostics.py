"""Fail-closed navigation diagnostics; safe for proxy credentials and URLs.

Certificate details are collected ONLY after normal hostname and CA
verification succeeded. A mismatched cert cannot have its SAN retrieved by
Python ssl in verified mode; the failing verification code is recorded instead.
"""
from __future__ import annotations

import re
import socket
import ssl
from urllib.parse import urlsplit


ERROR_MESSAGES = {
    "ERR_PROXY_CONNECTION_FAILED": ("proxy_connect", "Não foi possível conectar ao servidor proxy.", True),
    "ERR_INVALID_AUTH_CREDENTIALS": ("proxy_auth", "A proxy rejeitou as credenciais.", False),
    "ERR_TUNNEL_CONNECTION_FAILED": ("proxy_connect", "O túnel CONNECT não foi estabelecido.", True),
    "ERR_CERT_COMMON_NAME_INVALID": ("tls_cert", "O certificado HTTPS não corresponde ao hostname solicitado.", False),
    "ERR_CERT_AUTHORITY_INVALID": ("tls_cert", "A autoridade certificadora do HTTPS não é confiável.", False),
    "ERR_NAME_NOT_RESOLVED": ("dns", "A resolução do nome falhou.", True),
    "ERR_TIMED_OUT": ("timeout", "A navegação excedeu o tempo de espera.", True),
}


def classify_error(error) -> tuple[str, str, str, bool]:
    match = re.search(r"\b(ERR_[A-Z0-9_]+)\b", str(error))
    code = match.group(1) if match else "NAVIGATION_FAILED"
    stage, explanation, transient = ERROR_MESSAGES.get(
        code, ("browser", "Falha de navegação não classificada.", False)
    )
    return code, stage, explanation, transient


def hostname_from_url(url: str) -> str:
    try:
        name = (urlsplit(str(url)).hostname or "").lower().strip(".")
        if re.fullmatch(r"[a-z0-9.-]{1,253}", name):
            return name
    except ValueError:
        pass
    return ""


def _connect_response(sock, *, size_limit=8192):
    """Read only through CRLFCRLF, preserving any subsequent tunneled bytes."""
    data = bytearray()
    while not data.endswith(b"\r\n\r\n"):
        if len(data) >= size_limit:
            raise OSError("proxy_headers_too_large")
        b = sock.recv(1)
        if not b:
            raise OSError("proxy_closed_connect")
        data.extend(b)
    first = bytes(data).split(b"\r\n", 1)[0]
    m = re.match(rb"^HTTP/1\.[01] ([0-9]{3})(?: |\r|$)", first)
    if not m:
        raise OSError("invalid_connect_reply")
    return int(m.group(1))


def _tls_certificate_on_socket(sock, host, timeout):
    with sock:
        sock.settimeout(timeout)
        with ssl.create_default_context().wrap_socket(
            sock, server_hostname=host
        ) as secure:
            peer = secure.getpeercert()
            names = [
                value for key, value in peer.get("subjectAltName", ())
                if key == "DNS" and isinstance(value, str)
            ]
            # SAN names come from an untrusted remote certificate: safe
            # character set, short values, and a bounded count.
            names = [
                name[:120] for name in names[:12]
                if re.fullmatch(r"[A-Za-z0-9.*_-]{1,120}(?:\.[A-Za-z0-9.*_-]{1,120})*", name)
            ]
            return {"status": "validated", "sans": names, "hostname": host}


def _diagnose_trusted_tls_via_provider(proxy, hostname, timeout):
    from Settings.session_proxy_gateway import _open_tunnel
    sock = _open_tunnel(proxy, hostname, 443, timeout)
    return _tls_certificate_on_socket(sock, hostname, timeout)


def _diagnose_trusted_tls_via_local(gateway, hostname, timeout):
    if gateway is None or gateway.server is None:
        raise OSError("local_gateway_unavailable")
    port = gateway.server.server_address[1]
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall((
            f"CONNECT {hostname}:443 HTTP/1.1\r\n"
            f"Host: {hostname}:443\r\n"
            "Connection: keep-alive\r\n\r\n"
        ).encode("ascii"))
        status = _connect_response(sock)
        if status != 200:
            raise OSError("local_connect_rejected_" + str(status))
        return _tls_certificate_on_socket(sock, hostname, timeout)
    except Exception:
        sock.close()
        raise


def _safe_tls_error(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        code = getattr(exc, "verify_code", None)
        return "cert_verify_" + str(code) if isinstance(code, int) else "cert_verify_failed"
    if isinstance(exc, ssl.SSLError):
        reason = getattr(exc, "reason", "") or ""
        return reason if re.fullmatch(r"[A-Z0-9_]{1,55}", str(reason)) else "tls_handshake_failed"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, OSError):
        return "network_or_connect_error"
    return type(exc).__name__[:48]


def compare_certificates(proxy, gateway, requested_host, log, prefix):
    """Compare two VERIFIED TLS handshakes; never bypass certificate checks.

    Rotating upstreams may choose different exits on each call. A failure
    reports verification code only; SAN is unavailable without trusting an
    invalid certificate and will NOT be fabricated.
    """
    if not isinstance(requested_host, str) or not re.fullmatch(
        r"[a-z0-9.-]{1,253}", requested_host
    ):
        log(f"{prefix} [TLS] Hostname inválido ou indisponível; diagnóstico cancelado.")
        return {}
    outcome = {}
    for label, resolver in (
        ("provedor", lambda: _diagnose_trusted_tls_via_provider(proxy, requested_host, 6)),
        ("gateway_local", lambda: _diagnose_trusted_tls_via_local(gateway, requested_host, 6)),
    ):
        try:
            result = resolver()
            sans = ",".join(result["sans"])[:700] or "(não informados)"
            log(f"{prefix} [TLS] caminho={label} host={requested_host} CONNECT=200; certificado=validado; SAN={sans}.")
            outcome[label] = "validated"
        except Exception as exc:
            reason = _safe_tls_error(exc)
            log(
                f"{prefix} [TLS] caminho={label} host={requested_host} "
                f"certificado=recusado; causa={reason}; SAN=indisponível sem ignorar TLS."
            )
            outcome[label] = reason
    log(f"{prefix} [TLS] Comparação informativa: proxies rotativas podem usar saídas diferentes.")
    return outcome
