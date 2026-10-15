"""Per-browser loopback proxy: isolated credentials, upstream auth and fail-closed routing.

Reuses the Nexora HTTP proxy gateway helpers. Each Chromium instance gets an
independent localhost port and upstream credentials; no endpoint is shared by idx.
Never forwards a request directly if an upstream proxy was configured.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import socketserver
import ssl
import threading
from urllib.parse import urlsplit

from Settings.local_proxy_gateway import (
    _recv_headers, _split_header_body, _remove_proxy_auth_headers,
    _parse_request_line, _proxy_auth_header, _pipe_bidirectional,
)


class ProxyConnectionError(OSError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _read_exact(sock, length):
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ProxyConnectionError("connection_closed")
        buf.extend(chunk)
    return bytes(buf)


def _open_tunnel(proxy, host: str, port: int, timeout=8):
    """Connect through upstream proxy, never directly to the target."""
    url = urlsplit(proxy.server)
    protocol = url.scheme.lower()
    upstream_host, upstream_port = url.hostname, url.port
    if not upstream_host or not upstream_port or protocol not in ("http", "https", "socks5"):
        raise ProxyConnectionError("invalid_proxy")
    sock = socket.create_connection((upstream_host, upstream_port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        if protocol == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=upstream_host)
            sock.settimeout(timeout)
        if protocol in ("http", "https"):
            headers = [
                f"CONNECT {host}:{port} HTTP/1.1\r\n".encode("ascii"),
                f"Host: {host}:{port}\r\n".encode("ascii"),
            ]
            if proxy.username:
                headers.append(_proxy_auth_header(proxy.username, proxy.password or ""))
            sock.sendall(b"".join(headers) + b"Proxy-Connection: Keep-Alive\r\n\r\n")
            response = _recv_headers(sock)
            status_line = response.split(b"\r\n", 1)[0]
            if b" 200 " not in status_line:
                if b" 407 " in status_line:
                    raise ProxyConnectionError("auth_rejected")
                raise ProxyConnectionError("connect_rejected")
        else:
            # RFC 1928 + RFC 1929 (SOCKS5 username/password, when needed).
            auth = bool(proxy.username)
            sock.sendall(b"\x05\x02\x00\x02" if auth else b"\x05\x01\x00")
            response = _read_exact(sock, 2)
            if response[0] != 5 or response[1] == 255:
                raise ProxyConnectionError("socks_auth_unsupported")
            if response[1] == 2:
                username = (proxy.username or "").encode("utf-8")
                password = (proxy.password or "").encode("utf-8")
                if not auth or len(username) > 255 or len(password) > 255:
                    raise ProxyConnectionError("socks_auth_unsupported")
                sock.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
                if _read_exact(sock, 2) != b"\x01\x00":
                    raise ProxyConnectionError("auth_rejected")
            elif response[1] != 0:
                raise ProxyConnectionError("socks_auth_unsupported")
            domain = host.encode("idna")
            if len(domain) > 255:
                raise ProxyConnectionError("invalid_target")
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + port.to_bytes(2, "big"))
            reply = _read_exact(sock, 4)
            if reply[0] != 5 or reply[1] != 0:
                raise ProxyConnectionError("connect_rejected")
            if reply[3] == 1:
                _read_exact(sock, 4)
            elif reply[3] == 3:
                _read_exact(sock, _read_exact(sock, 1)[0])
            elif reply[3] == 4:
                _read_exact(sock, 16)
            else:
                raise ProxyConnectionError("connect_rejected")
            _read_exact(sock, 2)
        return sock
    except Exception:
        sock.close()
        raise


# Independent, well-known HTTPS host: do not let a destination site's
# certificate issue be misreported as a failing proxy. The browser still
# verifies the actual destination separately during page.goto.
PROXY_PROBE_HOST = "example.com"


def probe_proxy(proxy, *, target=PROXY_PROBE_HOST, timeout=8):
    """Test proxy authentication, CONNECT and trusted TLS in distinct stages.

    Never disables certificate checks, falls back to direct traffic or prints
    the upstream proxy credentials.
    """
    stage = "connect"
    try:
        with _open_tunnel(proxy, target, 443, timeout) as tunnel:
            stage = "tls_destination"
            with ssl.create_default_context().wrap_socket(
                tunnel, server_hostname=target
            ) as secure:
                secure.settimeout(timeout)
                # TLS handshake completed. A site-specific HEAD/GET is not
                # needed and sites may legitimately reject such requests.
        return {"reachable": True, "status": "ok",
                "message": "Autenticação, CONNECT e certificado HTTPS verificados."}
    except ProxyConnectionError as exc:
        labels = {
            "auth_rejected": "Autenticação da proxy recusada (HTTP 407 ou SOCKS5).",
            "connect_rejected": "A proxy recusou o túnel HTTPS.",
            "socks_auth_unsupported": "Método de autenticação SOCKS5 não aceito.",
            "invalid_proxy": "Configuração da proxy inválida.",
        }
        return {"reachable": False, "status": exc.code, "stage": stage,
                "message": labels.get(exc.code, "Falha de comunicação com a proxy.")}
    except ssl.SSLCertVerificationError as exc:
        verification_code = getattr(exc, "verify_code", None)
        suffix = f" (código de verificação {verification_code})" if isinstance(verification_code, int) else ""
        return {"reachable": False, "status": "tls_certificate_invalid", "stage": stage,
                "message": "O certificado HTTPS não foi validado" + suffix
                           + ". Verifique a cadeia de certificados do Windows/agente ou do destino de teste."}
    except ssl.SSLError as exc:
        raw_reason = str(getattr(exc, "reason", "") or "")
        # Only expose a short OpenSSL code; never forward exception details
        # containing addresses, credentials or CONNECT authorization.
        reason = raw_reason if len(raw_reason) <= 64 and all(
            char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in raw_reason
        ) else "TLS_HANDSHAKE_ERROR"
        return {"reachable": False, "status": "tls_handshake_failed", "stage": stage,
                "message": f"Negociação TLS não concluída na etapa {stage} ({reason})."}
    except socket.gaierror:
        return {"reachable": False, "status": "dns_error", "stage": stage,
                "message": "Não foi possível resolver o DNS da proxy ou do destino."}
    except (TimeoutError, socket.timeout):
        return {"reachable": False, "status": "timeout", "stage": stage,
                "message": "A proxy ou o destino demorou demais para responder."}
    except ConnectionRefusedError:
        return {"reachable": False, "status": "connection_refused", "stage": stage,
                "message": "A conexão com a porta da proxy foi recusada."}
    except (OSError, ValueError):
        return {"reachable": False, "status": "network_error", "stage": stage,
                "message": "Não foi possível estabelecer a conexão pela proxy."}


def probe_exit_ip(proxy, timeout=6):
    """Optional provider-routed HTTPS IP lookup; never use a direct connection."""
    try:
        with _open_tunnel(proxy, "api.ipify.org", 443, timeout) as tunnel:
            with ssl.create_default_context().wrap_socket(
                tunnel, server_hostname="api.ipify.org"
            ) as secure:
                secure.settimeout(timeout)
                secure.sendall(
                    b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n"
                )
                data = bytearray()
                while len(data) < 8192:
                    chunk = secure.recv(1024)
                    if not chunk:
                        break
                    data.extend(chunk)
        header, separator, body = bytes(data).partition(b"\r\n\r\n")
        if not separator or b" 200 " not in header.split(b"\r\n", 1)[0]:
            return ""
        address = body.decode("ascii", errors="ignore").strip()
        return str(ipaddress.ip_address(address))
    except (OSError, TimeoutError, ValueError, UnicodeError):
        return ""




def _validated_ip_from_text(raw):
    """Only accept an actual IP address, never an arbitrary response string."""
    value = str(raw or "").strip()
    try:
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            value = str(decoded.get("ip") or "").strip()
    except (ValueError, TypeError):
        pass
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def verify_browser_exit_ip(page, gateway, *, timeout_ms=10000):
    """Verify the *Chromium page's* public IP travelled via this session gateway.

    Uses the same page immediately before the requested destination navigation,
    so there is no secondary context, independent HTTP client or temporary tab.
    """
    targets = (
        ("api.ipify.org", "https://api.ipify.org?format=json"),
        ("icanhazip.com", "https://icanhazip.com/"),
        ("api64.ipify.org", "https://api64.ipify.org?format=json"),
    )
    saw_direct_success = False
    for host, url in targets:
        previous = gateway.connect_count(host)
        try:
            separator = "&" if "?" in url else "?"
            response = page.goto(
                url + separator + "nexora_check=" + secrets.token_hex(6),
                wait_until="domcontentloaded", timeout=timeout_ms,
            )
            if response is None or response.status != 200:
                continue
            raw = page.locator("body").inner_text(timeout=4000)
            ip_value = _validated_ip_from_text(raw)
            if not ip_value:
                continue
            if gateway.connect_count(host) <= previous:
                saw_direct_success = True
                continue
            return ip_value
        except Exception:
            # Try another known endpoint, but never continue to the user's
            # destination without proof that Chromium used this gateway.
            continue
    if saw_direct_success:
        raise ProxyConnectionError("browser_proxy_bypassed")
    raise ProxyConnectionError("browser_proxy_unverified")


def refresh_browser_exit_ip(page, gateway, *, timeout_ms=6000):
    """Non-navigating best-effort refresh from inside the active browser page.

    CSP or CORS on some target websites may reject cross-origin fetch; retain
    the last verified address rather than inventing a current IP in that case.
    """
    host = "api.ipify.org"
    before = gateway.connect_count(host)
    try:
        raw = page.evaluate(
            """async ({url, timeoutMs}) => {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), timeoutMs);
                try {
                    const response = await fetch(url, {
                        cache: 'no-store', credentials: 'omit',
                        signal: controller.signal,
                    });
                    return response.ok ? await response.text() : '';
                } finally {
                    clearTimeout(timeout);
                }
            }""",
            {"url": "https://api.ipify.org?format=json&nexora_check="
                     + secrets.token_hex(6), "timeoutMs": int(timeout_ms)},
        )
        ip_value = _validated_ip_from_text(raw)
        if ip_value:
            if gateway.connect_count(host) <= before:
                raise ProxyConnectionError("browser_proxy_bypassed")
            return ip_value
    except ProxyConnectionError:
        raise
    except Exception:
        # CSP/CORS or a network failure is not evidence of direct routing.
        pass
    return ""




class SessionProxyGateway:
    """One isolated HTTP local proxy per Chromium browser instance."""

    def __init__(self, proxy):
        if not proxy or not proxy.server:
            raise ProxyConnectionError("proxy_required")
        self.proxy = proxy
        self.server = None
        self.thread = None
        self._lock = threading.Lock()
        self._upstream_connects = {}

    def connect_count(self, host):
        """Only count successfully authenticated upstream CONNECT tunnels."""
        with self._lock:
            return self._upstream_connects.get(host.lower(), 0)

    def start(self):
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                client = self.request
                client.settimeout(30)
                try:
                    packet = _recv_headers(client)
                    if not packet or len(packet) > 65536:
                        return
                    head, body = _split_header_body(packet)
                    # Loopback-only ephemeral port belongs to this context.
                    # Never ask Chromium to authenticate to another proxy;
                    # only this gateway authenticates with the upstream.
                    method, target, version = _parse_request_line(head)
                    if method.upper() == "CONNECT":
                        if ":" not in target:
                            raise ProxyConnectionError("invalid_target")
                        host, port_raw = target.rsplit(":", 1)
                        upstream = _open_tunnel(owner.proxy, host, int(port_raw))
                        with owner._lock:
                            key = host.lower()
                            owner._upstream_connects[key] = owner._upstream_connects.get(key, 0) + 1
                        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                        if body:
                            upstream.sendall(body)
                        _pipe_bidirectional(client, upstream)
                    else:
                        parsed = urlsplit(target)
                        if parsed.scheme != "http" or not parsed.hostname:
                            raise ProxyConnectionError("invalid_target")
                        port = parsed.port or 80
                        if urlsplit(owner.proxy.server).scheme.lower() in ("http", "https"):
                            proxy_url = urlsplit(owner.proxy.server)
                            upstream = socket.create_connection((proxy_url.hostname, proxy_url.port), timeout=8)
                            if proxy_url.scheme == "https":
                                upstream = ssl.create_default_context().wrap_socket(
                                    upstream, server_hostname=proxy_url.hostname
                                )
                            upstream.settimeout(30)
                            clean = _remove_proxy_auth_headers(head)
                            if owner.proxy.username:
                                clean = clean.replace(
                                    b"\r\n\r\n",
                                    b"\r\n" + _proxy_auth_header(
                                        owner.proxy.username, owner.proxy.password or ""
                                    ) + b"\r\n", 1
                                )
                            upstream.sendall(clean + body)
                        else:
                            upstream = _open_tunnel(owner.proxy, parsed.hostname, port)
                            path = parsed.path or "/"
                            if parsed.query:
                                path += "?" + parsed.query
                            clean = _remove_proxy_auth_headers(head)
                            lines = clean.split(b"\r\n")
                            lines[0] = f"{method} {path} {version}".encode("ascii")
                            upstream.sendall(b"\r\n".join(lines) + body)
                        _pipe_bidirectional(client, upstream)
                except (OSError, ValueError, ProxyConnectionError):
                    try:
                        client.sendall(
                            b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n"
                            b"Content-Length: 0\r\n\r\n"
                        )
                    except OSError:
                        pass

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def playwright_proxy(self):
        if self.server is None:
            raise RuntimeError("Gateway de sessão ainda não iniciado.")
        return {"server": f"http://127.0.0.1:{self.server.server_address[1]}"}

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
