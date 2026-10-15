"""Offline tests for proxy-only routing, exact CONNECT, certificate safety.

No live proxy credentials or public websites are used by these tests.
"""
import re
from types import SimpleNamespace
import socket

import pytest

from Settings import local_proxy_gateway as legacy
from Settings import session_proxy_gateway as isolated
from Settings.proxy_diagnostics import classify_error, _connect_response, hostname_from_url


class FakeClient:
    def __init__(self):
        self.data = bytearray()

    def sendall(self, data):
        self.data.extend(data)


@pytest.mark.parametrize("method", ["CONNECT", "GET"])
def test_legacy_gateway_never_uses_direct_socket_when_upstream_missing(monkeypatch, method):
    monkeypatch.setattr(legacy, "_resolve_upstream_for_idx", lambda idx: None)
    calls = []

    def direct_is_forbidden(host, port):
        calls.append((host, port))
        raise AssertionError("DIRECT fallback forbidden")

    monkeypatch.setattr(legacy, "_open_socket", direct_is_forbidden)
    client = FakeClient()
    if method == "CONNECT":
        legacy._handle_connect(client, "valid.example:443", b"", 42)
    else:
        legacy._handle_plain_http(
            client,
            b"GET http://valid.example/path HTTP/1.1\r\n"
            b"Host: valid.example\r\n\r\n",
            b"", 42,
        )
    assert bytes(client.data).startswith(b"HTTP/1.1 502 Bad Gateway")
    assert calls == []


def test_legacy_gateway_routes_connect_through_provider(monkeypatch):
    from Settings.Criar_Mae.settings.browser import ProxyConfig
    upstream = {"server": "http://proxy.test:8181", "username": "user", "password": "secret"}
    monkeypatch.setattr(legacy, "_resolve_upstream_for_idx", lambda idx: upstream)
    forwarded = []

    def open_upstream(config, hostname, port):
        assert isinstance(config, ProxyConfig)
        assert config.server == upstream["server"]
        assert config.username == "user"
        assert config.password == "secret"
        forwarded.append((hostname, port))
        return object()

    monkeypatch.setattr(isolated, "_open_tunnel", open_upstream)
    monkeypatch.setattr(legacy, "_pipe_bidirectional", lambda client, sock: None)
    client = FakeClient()
    legacy._handle_connect(client, "valid.example:443", b"", 1)
    assert forwarded == [("valid.example", 443)]
    assert bytes(client.data).startswith(b"HTTP/1.1 200 Connection Established")


def test_legacy_http_through_upstream_sends_credentials_only_to_proxy(monkeypatch):
    upstream = {"server": "http://proxy.test:8181", "username": "demo", "password": "test-password"}
    monkeypatch.setattr(legacy, "_resolve_upstream_for_idx", lambda idx: upstream)
    calls = []

    class Upstream:
        def sendall(self, payload):
            calls.append(payload)

    monkeypatch.setattr(legacy, "_open_socket", lambda host, port: Upstream())
    monkeypatch.setattr(legacy, "_pipe_bidirectional", lambda client, remote: None)
    request = (b"GET http://example.test/a HTTP/1.1\r\n"
               b"Host: example.test\r\n"
               b"Proxy-Authorization: Basic invalid-client-token\r\n\r\n")
    legacy._handle_plain_http(FakeClient(), request, b"", 5)
    assert calls
    assert b"invalid-client-token" not in calls[0]
    assert b"Proxy-Authorization: Basic " in calls[0]
    assert b"demo:test-password" not in calls[0]


@pytest.mark.parametrize("message,expected,transient", [
    ("net::ERR_PROXY_CONNECTION_FAILED", "proxy_connect", True),
    ("net::ERR_INVALID_AUTH_CREDENTIALS", "proxy_auth", False),
    ("net::ERR_TUNNEL_CONNECTION_FAILED", "proxy_connect", True),
    ("net::ERR_CERT_COMMON_NAME_INVALID", "tls_cert", False),
    ("net::ERR_CERT_AUTHORITY_INVALID", "tls_cert", False),
    ("net::ERR_NAME_NOT_RESOLVED", "dns", True),
    ("net::ERR_TIMED_OUT", "timeout", True),
])
def test_chromium_network_error_messages(message, expected, transient):
    code, stage, explanation, can_retry = classify_error(message)
    assert stage == expected
    assert can_retry is transient
    assert explanation
    assert code in message


def test_navigation_error_never_echoes_query_credentials():
    code, stage, message, retryable = classify_error(
        "net::ERR_CERT_COMMON_NAME_INVALID at "
        "https://example.test/?password=sensitive-token"
    )
    assert code == "ERR_CERT_COMMON_NAME_INVALID"
    assert "sensitive" not in message
    assert hostname_from_url("https://example.test/?password=sensitive-token") == "example.test"


class BytewiseSocket:
    def __init__(self, data):
        self.incoming = bytearray(data)
        self.calls = []

    def recv(self, size):
        self.calls.append(size)
        if not self.incoming:
            return b""
        n = min(size, len(self.incoming))
        chunk = self.incoming[:n]
        del self.incoming[:n]
        return bytes(chunk)


def test_connect_reader_preserves_first_encrypted_byte_and_parses_code():
    sock = BytewiseSocket(b"HTTP/1.1 200 Connection Established\r\n\r\n\x16\x03\x01")
    assert _connect_response(sock) == 200
    assert sock.recv(4) == b"\x16\x03\x01"


def test_connect_reader_detects_proxy_auth_rejection():
    sock = BytewiseSocket(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
    assert _connect_response(sock) == 407


def test_compare_certificate_uses_verified_tls_and_reports_sans(monkeypatch):
    from Settings import proxy_diagnostics as diag
    messages = []
    monkeypatch.setattr(
        diag, "_diagnose_trusted_tls_via_provider",
        lambda proxy, hostname, timeout: {
            "status": "validated", "sans": ["good.example.test"],
            "hostname": hostname,
        },
    )
    monkeypatch.setattr(
        diag, "_diagnose_trusted_tls_via_local",
        lambda gateway, hostname, timeout: {
            "status": "validated", "sans": ["good.example.test"],
            "hostname": hostname,
        },
    )
    result = diag.compare_certificates(
        object(), object(), "good.example.test", messages.append, "[#1]"
    )
    assert result == {"provedor": "validated", "gateway_local": "validated"}
    assert any("SAN=good.example.test" in line for line in messages)


def test_compare_cert_failure_does_not_disable_tls_or_print_secrets(monkeypatch):
    from Settings import proxy_diagnostics as diag
    import ssl
    seen = []
    err = ssl.SSLCertVerificationError(1, "wrong certificate")
    err.verify_code = 62
    monkeypatch.setattr(diag, "_diagnose_trusted_tls_via_provider",
                        lambda *args: (_ for _ in ()).throw(err))
    monkeypatch.setattr(diag, "_diagnose_trusted_tls_via_local",
                        lambda *args: (_ for _ in ()).throw(err))
    results = diag.compare_certificates(
        object(), object(), "good.example.test?password=secret",
        seen.append, "[#1]",
    )
    assert results["provedor"] == "cert_verify_62"
    assert all("password" not in line for line in seen)
    assert all("SAN=indisponível" in line for line in seen if "certificado=recusado" in line)
