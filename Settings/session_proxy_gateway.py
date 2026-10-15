"""Per-browser loopback proxy: isolated credentials, upstream auth and fail-closed routing.

Reuses the Nexora HTTP proxy gateway helpers. Each Chromium instance gets an
independent localhost port and upstream credentials; no endpoint is shared by idx.
Never forwards a request directly if an upstream proxy was configured.
"""
from __future__ import annotations

import ipaddress
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


def probe_proxy(proxy, *, target="example.com", timeout=8):
    """Authenticated CONNECT + real TLS verification (not just a TCP port check)."""
    try:
        with _open_tunnel(proxy, target, 443, timeout) as tunnel:
            with ssl.create_default_context().wrap_socket(tunnel, server_hostname=target) as secure:
                secure.settimeout(timeout)
                # TLS handshake completed through the proxy; no site-specific
                # HEAD/GET is needed and sites may legitimately reject HEAD.
        return {"reachable": True, "status": "ok",
                "message": "Autenticação, túnel CONNECT e HTTPS verificados via proxy."}
    except ProxyConnectionError as exc:
        labels = {
            "auth_rejected": "Autenticação da proxy recusada (HTTP 407 ou SOCKS5).",
            "connect_rejected": "A proxy recusou o túnel HTTPS.",
            "socks_auth_unsupported": "Método de autenticação SOCKS5 não aceito.",
            "invalid_proxy": "Configuração da proxy inválida.",
        }
        return {"reachable": False, "status": exc.code,
                "message": labels.get(exc.code, "Falha de comunicação com a proxy.")}
    except ssl.SSLError:
        return {"reachable": False, "status": "tls_error",
                "message": "Falha ao verificar TLS pelo túnel da proxy."}
    except (OSError, TimeoutError, ValueError):
        return {"reachable": False, "status": "network_error",
                "message": "Proxy indisponível, DNS não resolvido ou tempo de conexão esgotado."}


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



class SessionProxyGateway:
    """One isolated HTTP local proxy per Chromium browser instance."""

    def __init__(self, proxy):
        if not proxy or not proxy.server:
            raise ProxyConnectionError("proxy_required")
        self.proxy = proxy
        self.server = None
        self.thread = None

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
                        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
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
