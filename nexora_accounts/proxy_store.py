"""Proxy registry for the Windows-local Nexora agent.

The ProxyBet preload exposes proxies:list, proxies:save and proxy:test IPC.
This is a new, independently implemented localhost equivalent, not copied
closed-source ProxyBet internals. Private credentials never enter API reads.
"""
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
from uuid import uuid4
from urllib.parse import urlsplit, unquote

_LOCK = threading.RLock()
_SCHEMES = {"http", "https", "socks5"}


class ProxyError(ValueError):
    """Public, non-secret configuration validation error."""


def _default_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Nexora" / "proxies.json"


def _text(value, field, maximum, required=False):
    if not isinstance(value, str):
        raise ProxyError(f"{field} inválido.")
    value = value.strip()
    if len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProxyError(f"{field} inválido.")
    if required and not value:
        raise ProxyError(f"Informe {field.lower()}.")
    return value


def _public(record):
    return {
        "id": record["id"],
        "name": record["name"],
        "host": record["host"],
        "port": record["port"],
        "protocol": record["protocol"],
        "username": record.get("username", ""),
        "hasPassword": bool(record.get("password")),
    }


def parse_proxy_line(raw):
    """Accept common one-line provider formats without guessing protocols.

    host:port[:user:password], user:password@host:port and
    http(s)://user:password@host:port or socks5://user:password@host:port.
    A missing scheme explicitly means HTTP.
    """
    line = _text(raw, "Linha da proxy", 1024, True)
    scheme = "http"
    body = line
    if "://" in line:
        scheme, body = line.split("://", 1)
        scheme = scheme.lower()
    if scheme == "socks":
        scheme = "socks5"
    if scheme not in _SCHEMES:
        raise ProxyError("Tipo de proxy não suportado. Use HTTP, HTTPS ou SOCKS5.")
    if not body:
        raise ProxyError("Formato inválido. Informe host:porta[:usuario:senha].")
    segments = body.split(":", 3)
    if len(segments) == 4 and segments[1].isdigit() and not body.startswith("["):
        host, port, username, password = segments
        fields = (host, port, username, password)
    else:
        try:
            url = urlsplit(scheme + "://" + body)
            if url.path or url.query or url.fragment:
                raise ProxyError("Informe apenas servidor, porta e credenciais; sem caminho na URL.")
            fields = (url.hostname or "", url.port or "", unquote(url.username or ""), unquote(url.password or ""))
        except ValueError as exc:
            raise ProxyError("Endereço ou porta da proxy inválido.") from exc
    host, port, username, password = fields
    host = _text(host, "Servidor", 253, True).lower()
    try:
        port = int(str(port))
    except ValueError as exc:
        raise ProxyError("Porta da proxy inválida.") from exc
    if not 1 <= port <= 65535:
        raise ProxyError("Porta da proxy fora do intervalo permitido.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?", host):
        raise ProxyError("Servidor da proxy inválido.")
    username = _text(username, "Usuário", 128)
    password = _text(password, "Senha", 256)
    return {
        "name": host[:65] + ":" + str(port),
        "host": host, "port": port,
        "protocol": scheme, "username": username, "password": password,
    }


class ProxyStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else _default_path()

    def _read(self):
        if not self.path.exists():
            return {"activeId": None, "items": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("bad shape")
            return data
        except (ValueError, OSError) as exc:
            raise ProxyError("Arquivo local de proxies inválido. Faça backup antes de alterar.") from exc

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(self.path.parent),
                prefix=".proxies-", suffix=".tmp", delete=False,
            ) as tmp:
                temp_name = tmp.name
                json.dump(state, tmp, ensure_ascii=False, indent=2)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def list(self):
        with _LOCK:
            state = self._read()
            return {
                "activeId": state.get("activeId"),
                "proxies": [_public(x) for x in state["items"]],
            }

    def save(self, payload):
        if not isinstance(payload, dict):
            raise ProxyError("Dados do proxy inválidos.")
        name = _text(payload.get("name", ""), "Nome", 80, True)
        host = _text(payload.get("host", ""), "Servidor", 253, True).lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?", host):
            raise ProxyError("Servidor deve ser um hostname ou endereço IPv4 válido.")
        try:
            port = int(str(payload.get("port", "")).strip())
        except (TypeError, ValueError) as exc:
            raise ProxyError("Porta inválida.") from exc
        if not 1 <= port <= 65535:
            raise ProxyError("Porta deve estar entre 1 e 65535.")
        scheme = _text(payload.get("protocol", "http"), "Protocolo", 12).lower()
        if scheme not in _SCHEMES:
            raise ProxyError("Protocolo não suportado.")
        username = _text(payload.get("username", ""), "Usuário", 128)
        if ":" in username:
            raise ProxyError("Usuário do proxy não pode conter dois-pontos.")
        supplied_password = payload.get("password", "")
        password = _text(supplied_password, "Senha", 256)
        identifier = _text(payload.get("id", ""), "ID", 80)
        with _LOCK:
            state = self._read()
            old = next((p for p in state["items"] if p["id"] == identifier), None)
            if identifier and old is None:
                raise ProxyError("Proxy não encontrado.")
            if not identifier:
                identifier = uuid4().hex
            if not password and old and not payload.get("clearPassword"):
                password = old.get("password", "")
            saved = {
                "id": identifier, "name": name, "host": host, "port": port,
                "protocol": scheme, "username": username, "password": password,
            }
            if old:
                state["items"] = [saved if p["id"] == identifier else p for p in state["items"]]
            else:
                if len(state["items"]) >= 200:
                    raise ProxyError("Limite de 200 proxies atingido.")
                state["items"].append(saved)
            self._write(state)
            return _public(saved)

    def save_line(self, line):
        result = self.save(parse_proxy_line(line))
        with _LOCK:
            state = self._read()
            state["managed"] = True
            self._write(state)
        return result

    def assign(self, count, *, profile_id=None):
        """Allocate one endpoint per browser, retaining the selected profile binding.

        A provider may rotate its exit IP; an endpoint assignment is not an IP
        guarantee. All assignments are copied before any browser starts.
        """
        if not isinstance(count, int) or count < 0:
            raise ProxyError("Quantidade de sessões inválida.")
        with _LOCK:
            state = self._read()
            items = state["items"]
            if not items:
                return []
            start = state.get("nextSlot", 0) % len(items)
            selected = [items[(start + i) % len(items)].copy() for i in range(count)]
            if profile_id is not None and count:
                bound_id = state.get("profileProxies", {}).get(str(profile_id))
                if bound_id:
                    bound = next((p for p in items if p["id"] == bound_id), None)
                    if bound is None:
                        raise ProxyError("A proxy vinculada a esta conta foi excluída; selecione outra.")
                    selected[0] = bound.copy()
                    others = [p for p in items if p["id"] != bound_id]
                    if others:
                        for i in range(1, count):
                            selected[i] = others[(start + i - 1) % len(others)].copy()
            state["nextSlot"] = (start + count) % len(items)
            self._write(state)
            return selected

    def bind_profile(self, profile_id, proxy_id):
        from nexora_accounts.local_profiles import LocalProfileStore
        if isinstance(profile_id, bool) or not isinstance(profile_id, int):
            raise ProxyError("ID do perfil inválido.")
        if not any(p["id"] == profile_id for p in LocalProfileStore().list()):
            raise ProxyError("Perfil não encontrado.")
        with _LOCK:
            state = self._read()
            if proxy_id is not None and not any(p["id"] == proxy_id for p in state["items"]):
                raise ProxyError("Proxy para este perfil não encontrada.")
            bindings = state.setdefault("profileProxies", {})
            if proxy_id is None:
                bindings.pop(str(profile_id), None)
            else:
                bindings[str(profile_id)] = proxy_id
                state["managed"] = True
            self._write(state)
            return {"profileId": profile_id, "proxyId": proxy_id}

    def profile_bindings(self):
        with _LOCK:
            return dict(self._read().get("profileProxies", {}))

    def test_authenticated(self, identifier, *, target="example.com", timeout=8):
        with _LOCK:
            entry = next((p.copy() for p in self._read()["items"] if p["id"] == identifier), None)
        if entry is None:
            raise ProxyError("Proxy não encontrada.")
        from Settings.Criar_Mae.settings.browser import ProxyConfig
        from Settings.session_proxy_gateway import probe_proxy
        return probe_proxy(
            ProxyConfig(
                server=f"{entry['protocol']}://{entry['host']}:{entry['port']}",
                username=entry.get("username") or None,
                password=entry.get("password") or None,
            ), target=target, timeout=timeout,
        )

    def delete(self, identifier):
        with _LOCK:
            state = self._read()
            before = len(state["items"])
            state["items"] = [x for x in state["items"] if x["id"] != identifier]
            if len(state["items"]) == before:
                raise ProxyError("Proxy não encontrado.")
            if state.get("activeId") == identifier:
                state["activeId"] = None
            # Deleted endpoints cannot silently remain bound to accounts.
            bindings = state.get("profileProxies", {})
            state["profileProxies"] = {k: v for k, v in bindings.items() if v != identifier}
            self._write(state)
            return {"ok": True}

    def activate(self, identifier):
        with _LOCK:
            state = self._read()
            if identifier is not None and not any(p["id"] == identifier for p in state["items"]):
                raise ProxyError("Proxy não encontrado.")
            state["activeId"] = identifier
            state["managed"] = True
            self._write(state)
            return {"activeId": identifier}

    def is_managed(self):
        """True once the user has chosen Usar or Desativar in the Proxy tab.

        Explicitly disabling the manager must not resurrect an old hidden
        Mãe/Filha proxy_raw value on the next browser launch.
        """
        with _LOCK:
            return self._read().get("managed", False) is True

    def active(self):
        with _LOCK:
            state = self._read()
            return next((p.copy() for p in state["items"]
                         if p["id"] == state.get("activeId")), None)

    def test(self, identifier, *, connect=socket.create_connection):
        with _LOCK:
            state = self._read()
            record = next((p.copy() for p in state["items"] if p["id"] == identifier), None)
        if record is None:
            raise ProxyError("Proxy não encontrado.")
        # TCP-only reachability: never claim authentication, IP or CONNECT works.
        try:
            sock = connect((record["host"], record["port"]), timeout=4)
            sock.close()
            return {"reachable": True, "message": "Porta TCP acessível. Autenticação e saída pela rede ainda não verificadas."}
        except (OSError, TimeoutError):
            return {"reachable": False, "message": "Não foi possível conectar à porta do proxy."}


def active_proxy_config():
    """Return existing browser.ProxyConfig; none when the manager is inactive."""
    item = ProxyStore().active()
    if item is None:
        return None
    from Settings.Criar_Mae.settings.browser import ProxyConfig
    return ProxyConfig(
        server=f"{item['protocol']}://{item['host']}:{item['port']}",
        username=item.get("username") or None,
        password=item.get("password") or None,
    )
