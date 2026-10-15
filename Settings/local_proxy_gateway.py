import base64
import select
import socket
import threading
from typing import Optional
from urllib.parse import urlsplit

from Settings.Criar_Mae.settings.utils import load_settings
from Settings.Criar_Mae.settings.browser import (
    parse_proxy_string,
    build_upstream_proxy_dict,
    LOCAL_PROXY_GATEWAY_HOST,
    LOCAL_PROXY_GATEWAY_PORT,
    LOCAL_PROXY_GATEWAY_PASSWORD,
)

AREA_PIX_SCOPE = "mae"
BUFFER_SIZE = 65536


def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"


def _read_settings() -> dict:
    try:
        st = load_settings()
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _gateway_proxy_enabled() -> bool:
    # An explicitly selected workspace proxy is enabled regardless of the
    # legacy hidden tab toggle. Legacy settings remain a fallback.
    from nexora_accounts.proxy_store import ProxyStore
    if ProxyStore().active() is not None:
        return True
    st = _read_settings()
    return bool(st.get(_scope_key("proxy_enabled"), True))


def _raw_upstream_proxy() -> str:
    st = _read_settings()
    return str(st.get("proxy_raw") or "").strip()


def _extract_basic_auth_value(headers: bytes, name: bytes) -> str:
    header_name = name.lower() + b":"
    for line in headers.split(b"\r\n"):
        low = line.lower()
        if low.startswith(header_name):
            try:
                return line.split(b":", 1)[1].strip().decode("utf-8", errors="ignore")
            except Exception:
                return ""
    return ""


def _extract_client_idx(headers: bytes) -> Optional[int]:
    auth = _extract_basic_auth_value(headers, b"Proxy-Authorization")
    if not auth.lower().startswith("basic "):
        return None

    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", errors="ignore")
    except Exception:
        return None

    if ":" not in decoded:
        return None

    username, password = decoded.split(":", 1)
    if password != LOCAL_PROXY_GATEWAY_PASSWORD:
        return None

    username = username.strip()
    if username.startswith("idx-"):
        raw_idx = username[4:].strip()
        if raw_idx.isdigit():
            return int(raw_idx)

    return None


def _resolve_upstream_for_idx(idx: int) -> Optional[dict]:
    if not _gateway_proxy_enabled():
        return None

    from nexora_accounts.proxy_store import active_proxy_config
    selected = active_proxy_config()
    if selected is not None:
        return build_upstream_proxy_dict(selected, idx=idx)

    raw = _raw_upstream_proxy()
    if not raw:
        return None

    parsed = parse_proxy_string(raw)
    return build_upstream_proxy_dict(parsed, idx=idx)


def _proxy_auth_header(username: str, password: str) -> bytes:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {token}\r\n".encode("utf-8")


def _recv_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            break
    return data


def _split_header_body(packet: bytes) -> tuple[bytes, bytes]:
    marker = b"\r\n\r\n"
    pos = packet.find(marker)
    if pos < 0:
        return packet, b""
    head = packet[:pos + 4]
    body = packet[pos + 4:]
    return head, body


def _remove_proxy_auth_headers(header_block: bytes) -> bytes:
    cleaned = []
    for line in header_block.split(b"\r\n"):
        low = line.lower()
        if low.startswith(b"proxy-authorization:"):
            continue
        if low.startswith(b"proxy-connection:"):
            continue
        cleaned.append(line)
    return b"\r\n".join(cleaned)


def _parse_request_line(header_block: bytes) -> tuple[str, str, str]:
    first_line = header_block.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
    parts = first_line.split(" ")
    if len(parts) < 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def _extract_host_from_headers(header_block: bytes) -> str:
    for line in header_block.split(b"\r\n"):
        low = line.lower()
        if low.startswith(b"host:"):
            try:
                return line.split(b":", 1)[1].strip().decode("utf-8", errors="ignore")
            except Exception:
                return ""
    return ""


def _open_socket(host: str, port: int) -> socket.socket:
    return socket.create_connection((host, port), timeout=20)


def _pipe_bidirectional(a: socket.socket, b: socket.socket):
    sockets = [a, b]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 60)
            if errored:
                break
            if not readable:
                continue

            for src in readable:
                dst = b if src is a else a
                try:
                    data = src.recv(BUFFER_SIZE)
                except Exception:
                    data = b""

                if not data:
                    return

                try:
                    dst.sendall(data)
                except Exception:
                    return
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def _handle_connect(client: socket.socket, target: str, headers: bytes, idx: int):
    if ":" not in target:
        client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        return

    host, port_str = target.rsplit(":", 1)
    port = int(port_str.strip())

    upstream = _resolve_upstream_for_idx(idx)

    if upstream:
        upstream_server = str(upstream.get("server") or "").strip()
        scheme, rest = upstream_server.split("://", 1) if "://" in upstream_server else ("http", upstream_server)
        proxy_host, proxy_port_str = rest.rsplit(":", 1)
        proxy_port = int(proxy_port_str)

        upstream_sock = _open_socket(proxy_host, proxy_port)

        req = [
            f"CONNECT {host}:{port} HTTP/1.1\r\n".encode("utf-8"),
            f"Host: {host}:{port}\r\n".encode("utf-8"),
        ]

        if upstream.get("username"):
            req.append(_proxy_auth_header(str(upstream["username"]), str(upstream.get("password") or "")))

        req.append(b"\r\n")
        upstream_sock.sendall(b"".join(req))

        upstream_resp = _recv_headers(upstream_sock)
        if b" 200 " not in upstream_resp.split(b"\r\n", 1)[0]:
            client.sendall(upstream_resp or b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            upstream_sock.close()
            return

        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        _pipe_bidirectional(client, upstream_sock)
        return

    remote = _open_socket(host, port)
    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    _pipe_bidirectional(client, remote)


def _handle_plain_http(client: socket.socket, header_block: bytes, body: bytes, idx: int):
    method, target, version = _parse_request_line(header_block)
    if not method or not target or not version:
        client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        return

    upstream = _resolve_upstream_for_idx(idx)

    if upstream:
        upstream_server = str(upstream.get("server") or "").strip()
        scheme, rest = upstream_server.split("://", 1) if "://" in upstream_server else ("http", upstream_server)
        proxy_host, proxy_port_str = rest.rsplit(":", 1)
        proxy_port = int(proxy_port_str)

        out = _remove_proxy_auth_headers(header_block)

        if upstream.get("username"):
            out = out.replace(
                b"\r\n\r\n",
                b"\r\n" + _proxy_auth_header(str(upstream["username"]), str(upstream.get("password") or "")) + b"\r\n",
                1,
            )

        upstream_sock = _open_socket(proxy_host, proxy_port)
        upstream_sock.sendall(out + body)
        _pipe_bidirectional(client, upstream_sock)
        return

    parsed = urlsplit(target)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not host:
        host_header = _extract_host_from_headers(header_block)
        if not host_header:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        if ":" in host_header:
            host, port_str = host_header.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_header
            port = 80

    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    clean = _remove_proxy_auth_headers(header_block)
    lines = clean.split(b"\r\n")
    lines[0] = f"{method} {path} {version}".encode("utf-8")
    out = b"\r\n".join(lines)

    remote = _open_socket(host, port)
    remote.sendall(out + body)
    _pipe_bidirectional(client, remote)

def _send_proxy_auth_required(client: socket.socket):
    try:
        client.sendall(
            b"HTTP/1.1 407 Proxy Authentication Required\r\n"
            b'Proxy-Authenticate: Basic realm="LocalProxyGateway"\r\n'
            b"Connection: close\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
    except Exception:
        pass

def _client_worker(client: socket.socket, address):
    try:
        packet = _recv_headers(client)
        if not packet:
            return

        idx = _extract_client_idx(packet)
        if idx is None:
            _send_proxy_auth_required(client)
            return

        header_block, body = _split_header_body(packet)
        method, target, _ = _parse_request_line(header_block)

        if method.upper() == "CONNECT":
            _handle_connect(client, target, header_block, idx)
        else:
            _handle_plain_http(client, header_block, body, idx)
    except Exception:
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except Exception:
            pass
    finally:
        try:
            client.close()
        except Exception:
            pass


def main(stop_event=None):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LOCAL_PROXY_GATEWAY_HOST, LOCAL_PROXY_GATEWAY_PORT))
    server.listen(200)
    server.settimeout(0.5)

    print(f"[gateway] ouvindo em {LOCAL_PROXY_GATEWAY_HOST}:{LOCAL_PROXY_GATEWAY_PORT}")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break

            try:
                client, address = server.accept()
            except socket.timeout:
                continue

            t = threading.Thread(target=_client_worker, args=(client, address), daemon=True)
            t.start()
    finally:
        try:
            server.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

