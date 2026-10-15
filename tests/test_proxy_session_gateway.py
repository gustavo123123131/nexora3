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


def test_https_probe_uses_destination_sni_and_reports_invalid_certificate(monkeypatch):
    """A cert error at the target is not a proxy authentication failure."""
    import ssl
    from contextlib import nullcontext
    from Settings import session_proxy_gateway as gateway

    monkeypatch.setattr(
        gateway, "_open_tunnel",
        lambda proxy, target, port, timeout: nullcontext(object()),
    )
    seen = []

    class BadCertificate:
        def wrap_socket(self, _sock, *, server_hostname):
            seen.append(server_hostname)
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")

    monkeypatch.setattr(gateway.ssl, "create_default_context", lambda: BadCertificate())
    result = gateway.probe_proxy(
        ProxyConfig("http://localhost:3120", "demo-user", "demo-password")
    )
    assert seen == ["example.com"]
    assert result["reachable"] is False
    assert result["status"] == "tls_certificate_invalid"
    assert result["stage"] == "tls_destination"
    assert "demo-password" not in str(result)


def test_https_probe_separates_tls_handshake_from_authentication(monkeypatch):
    """SSL handshake failures have their own classification and stage."""
    import ssl
    from contextlib import nullcontext
    from Settings import session_proxy_gateway as gateway

    monkeypatch.setattr(
        gateway, "_open_tunnel",
        lambda proxy, target, port, timeout: nullcontext(object()),
    )

    class BadHandshake:
        def wrap_socket(self, _sock, *, server_hostname):
            raise ssl.SSLError(1, "unexpected EOF")

    monkeypatch.setattr(gateway.ssl, "create_default_context", lambda: BadHandshake())
    result = gateway.probe_proxy(ProxyConfig("http://localhost:3120"))
    assert result["reachable"] is False
    assert result["status"] == "tls_handshake_failed"
    assert result["stage"] == "tls_destination"
    assert result["reachable"] is False
    assert result["connectVerified"] is True
    assert result["browserVerificationRequired"] is True
    assert "Negociação TLS" in result["message"]


def test_proxy_authenticated_connect_remains_fail_closed(monkeypatch):
    from Settings import session_proxy_gateway as gateway

    def reject(_proxy, _target, _port, _timeout):
        raise ProxyConnectionError("auth_rejected")

    monkeypatch.setattr(gateway, "_open_tunnel", reject)
    result = gateway.probe_proxy(
        ProxyConfig("http://localhost:3120", "demo-user", "demo-password")
    )
    assert result["reachable"] is False
    assert result["status"] == "auth_rejected"
    assert result["stage"] == "connect"
    assert result.get("browserVerificationRequired") is not True
    assert "demo-password" not in str(result)


class FakeBrowserGateway:
    def __init__(self):
        self.counts = {}

    def connect_count(self, host):
        return self.counts.get(host, 0)


class FakeIpLocator:
    def __init__(self, page):
        self.page = page

    def inner_text(self, *, timeout):
        return self.page.text


class FakeIpResponse:
    status = 200


class FakeIpPage:
    def __init__(self, gateway, *, routed=True, text='{"ip":"203.0.113.78"}'):
        self.gateway = gateway
        self.routed = routed
        self.text = text
        self.visited = []

    def goto(self, url, *, wait_until, timeout):
        from urllib.parse import urlsplit
        assert wait_until == "domcontentloaded"
        host = urlsplit(url).hostname
        self.visited.append(url)
        if self.routed:
            self.gateway.counts[host] = self.gateway.counts.get(host, 0) + 1
        return FakeIpResponse()

    def locator(self, selector):
        assert selector == "body"
        return FakeIpLocator(self)


def test_browser_ip_verifier_uses_actual_page_and_gateway_counter():
    from Settings.session_proxy_gateway import verify_browser_exit_ip
    gateway = FakeBrowserGateway()
    page = FakeIpPage(gateway)
    result = verify_browser_exit_ip(page, gateway)
    assert result == "203.0.113.78"
    assert len(page.visited) == 1
    assert page.visited[0].startswith("https://api.ipify.org?")
    assert gateway.connect_count("api.ipify.org") == 1


def test_browser_ip_verifier_detects_direct_bypass_and_blocks():
    import pytest
    from Settings.session_proxy_gateway import verify_browser_exit_ip
    gateway = FakeBrowserGateway()
    page = FakeIpPage(gateway, routed=False, text="203.0.113.78")
    with pytest.raises(ProxyConnectionError, match="browser_proxy_bypassed"):
        verify_browser_exit_ip(page, gateway)
    assert gateway.connect_count("api.ipify.org") == 0
    assert len(page.visited) == 3


def test_browser_ip_verifier_rejects_invalid_response_even_with_proxy():
    import pytest
    from Settings.session_proxy_gateway import verify_browser_exit_ip
    gateway = FakeBrowserGateway()
    page = FakeIpPage(gateway, text="not an IP")
    with pytest.raises(ProxyConnectionError, match="browser_proxy_unverified"):
        verify_browser_exit_ip(page, gateway)


def test_live_overlay_refresh_preserves_gateway_verification():
    from Settings.session_proxy_gateway import refresh_browser_exit_ip
    gateway = FakeBrowserGateway()

    class LivePage:
        def evaluate(self, script, arg):
            assert "fetch(url" in script
            gateway.counts["api.ipify.org"] = gateway.counts.get("api.ipify.org", 0) + 1
            return '{"ip":"198.51.100.23"}'

    assert refresh_browser_exit_ip(LivePage(), gateway) == "198.51.100.23"

    class DirectPage:
        def evaluate(self, script, arg):
            return '{"ip":"198.51.100.23"}'

    import pytest
    with pytest.raises(ProxyConnectionError, match="browser_proxy_bypassed"):
        refresh_browser_exit_ip(DirectPage(), gateway)

    class CspBlockedPage:
        def evaluate(self, script, arg):
            raise RuntimeError("Content Security Policy blocks cross-origin fetch")

    assert refresh_browser_exit_ip(CspBlockedPage(), gateway) == ""


def test_browser_verification_reports_sanitized_network_error():
    from Settings.session_proxy_gateway import verify_browser_exit_ip
    gateway = FakeBrowserGateway()

    class FailedBrowserPage:
        def goto(self, url, *, wait_until, timeout):
            raise RuntimeError(
                "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at "
                "https://some-secret.example/?token=never-display"
            )

    import pytest
    with pytest.raises(ProxyConnectionError) as failure:
        verify_browser_exit_ip(FailedBrowserPage(), gateway)
    assert failure.value.code == "browser_proxy_unverified"
    assert failure.value.detail == "ERR_TUNNEL_CONNECTION_FAILED"
    assert "secret" not in failure.value.detail


def test_tls_failure_during_upstream_connect_does_not_allow_browser_fallback(monkeypatch):
    import ssl
    from Settings import session_proxy_gateway as gateway
    monkeypatch.setattr(
        gateway, "_open_tunnel",
        lambda proxy, target, port, timeout: (_ for _ in ()).throw(
            ssl.SSLError(1, "upstream TLS failure")
        ),
    )
    result = gateway.probe_proxy(ProxyConfig("https://localhost:8443"))
    assert result["reachable"] is False
    assert result["status"] == "tls_handshake_failed"
    assert result["stage"] == "connect"
    assert result["browserVerificationRequired"] is False


def test_rotating_proxy_exit_ip_can_change_without_direct_bypass():
    from Settings.session_proxy_gateway import verify_browser_exit_ip
    gateway = FakeBrowserGateway()
    page = FakeIpPage(gateway, text='{"ip":"203.0.113.10"}')
    first = verify_browser_exit_ip(page, gateway)
    page.text = '{"ip":"198.51.100.28"}'
    second = verify_browser_exit_ip(page, gateway)
    assert first == "203.0.113.10"
    assert second == "198.51.100.28"
    assert gateway.connect_count("api.ipify.org") == 2


def test_upstream_refused_connect_is_not_confused_with_authentication():
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            received = b""
            while b"\\r\\n\\r\\n" not in received:
                data = self.request.recv(4096)
                if not data:
                    return
                received += data
            self.request.sendall(
                b"HTTP/1.1 403 Forbidden\\r\\nContent-Length: 0\\r\\n\\r\\n"
            )

    server = FakeUpstream(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        proxy = ProxyConfig(
            f"http://127.0.0.1:{server.server_address[1]}",
            "demo-user", "demo-password",
        )
        result = probe_proxy(proxy, target="example.test", timeout=3)
        assert result["status"] == "connect_rejected"
        assert result["stage"] == "connect"
        assert result["reachable"] is False
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
