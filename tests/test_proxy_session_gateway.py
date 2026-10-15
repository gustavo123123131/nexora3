"""Offline regression tests for provider authentication and fail-closed gateway.

Only a local fake HTTP proxy is contacted. No real provider or credentials.
"""
import base64
import socket
import socketserver
import threading

from Settings.Criar_Mae.settings.browser import ProxyConfig
from Settings.session_proxy_gateway import (
    _open_tunnel, probe_proxy, ProxyConnectionError, SessionProxyGateway,
)


class FakeUpstream(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def fake_proxy(*, accept=True):
    observed = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                data += chunk
            observed.append(data)
            if accept:
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                try:
                    self.request.recv(32)
                except (OSError, TimeoutError):
                    pass
            else:
                self.request.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b"Proxy-Authenticate: Basic realm=\"Fake\"\r\n\r\n"
                )

    server = FakeUpstream(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, observed


def test_http_connect_sends_basic_auth_only_to_provider():
    server, worker, observed = fake_proxy()
    try:
        proxy = ProxyConfig(f"http://127.0.0.1:{server.server_address[1]}",
                            "demo-user", "demo-password")
        with _open_tunnel(proxy, "example.org", 443, timeout=3):
            pass
        assert observed
        assert observed[0].startswith(b"CONNECT example.org:443 HTTP/1.1\r\n")
        expected = base64.b64encode(b"demo-user:demo-password")
        assert b"Proxy-Authorization: Basic " + expected in observed[0]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_connect_reports_auth_rejection_not_tcp_success():
    server, worker, observed = fake_proxy(accept=False)
    try:
        proxy = ProxyConfig(f"http://127.0.0.1:{server.server_address[1]}",
                            "demo-user", "bad-password")
        result = probe_proxy(proxy, target="example.org", timeout=3)
        assert result["reachable"] is False
        assert result["status"] == "auth_rejected"
        assert "bad-password" not in str(result)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_browser_gateway_uses_upstream_and_never_falls_back_direct():
    server, worker, observed = fake_proxy(accept=False)
    proxy = ProxyConfig(f"http://127.0.0.1:{server.server_address[1]}",
                        "demo-user", "bad-password")
    gateway = SessionProxyGateway(proxy).start()
    try:
        config = gateway.playwright_proxy()
        assert config.keys() == {"server"}  # no browser-side authentication
        port = int(config["server"].rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.settimeout(3)
            client.sendall(
                b"CONNECT example.org:443 HTTP/1.1\r\n"
                b"Host: example.org:443\r\n\r\n"
            )
            result = client.recv(1024)
            assert result.startswith(b"HTTP/1.1 502")
        assert observed
    finally:
        gateway.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
