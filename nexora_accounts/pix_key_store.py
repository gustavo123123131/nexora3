"""Local-only Pix key registry. Never contacts banks or the DICT.

The on-disk file is independent of accounts, proxies and browser profiles.
All reads/writes share both a process lock and an interprocess file lock.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import UUID, uuid4

_LOCK = threading.RLock()
_TYPES = frozenset({"CPF", "PHONE", "EMAIL", "EVP"})
_DDDS = frozenset((
    11,12,13,14,15,16,17,18,19,21,22,24,27,28,
    31,32,33,34,35,37,38,41,42,43,44,45,46,47,48,49,
    51,53,54,55,61,62,63,64,65,66,67,68,69,
    71,73,74,75,77,79,81,82,83,84,85,86,87,88,89,
    91,92,93,94,95,96,97,98,99,
))
_EMAIL = re.compile(
    r"^[a-z0-9.!#$'*+/=?^_`{|}~-]+@[a-z0-9]"
    r"(?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_EVP = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class PixKeyError(ValueError):
    """Safe public error without key data."""


def _default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Nexora" / "pix_keys.json"


def _cpf(raw: str) -> str | None:
    if not re.fullmatch(r"(?:[0-9]{11}|[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2})", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11 or len(set(digits)) == 1:
        return None
    for length in (9, 10):
        total = sum(int(digits[i]) * (length + 1 - i) for i in range(length))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if int(digits[length]) != check:
            return None
    return digits


def _phone(raw: str) -> str | None:
    if not re.fullmatch(r"\+?[0-9 ()\-.]+", raw):
        return None
    normalized = re.sub(r"[ ()\-.]", "", raw)
    if normalized.startswith("+"):
        if not normalized.startswith("+55"):
            return None
        normalized = normalized[3:]
    elif len(normalized) == 13 and normalized.startswith("55"):
        normalized = normalized[2:]
    if len(normalized) != 11 or not normalized.isascii() or not normalized.isdigit():
        return None
    if int(normalized[:2]) not in _DDDS or normalized[2] != "9":
        return None
    if len(set(normalized[2:])) == 1:
        return None
    return "+55" + normalized


def _email(raw: str) -> str | None:
    value = raw.lower()
    if len(value) > 77 or not _EMAIL.fullmatch(value):
        return None
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or "." not in domain or not re.search(r"\.[a-z]{2,63}$", domain):
        return None
    return value


def candidates(value: str) -> dict[str, str]:
    """Return matching types/normalized values, without guessing ambiguous digits."""
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return {}
    raw = value.strip()
    if not raw or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return {}
    found = {}
    cpf = _cpf(raw)
    phone = _phone(raw)
    email = _email(raw)
    if cpf:
        found["CPF"] = cpf
    if phone:
        found["PHONE"] = phone
    if email:
        found["EMAIL"] = email
    if _EVP.fullmatch(raw.lower()):
        found["EVP"] = raw.lower()
    return found


def normalize_key(value: str, key_type: str | None = None) -> tuple[str, str]:
    options = candidates(value)
    if not options:
        raise PixKeyError("Chave Pix inválida. Confira o formato do CPF, telefone, e-mail ou EVP.")
    if key_type is not None and (not isinstance(key_type, str) or key_type not in _TYPES):
        raise PixKeyError("Tipo de chave Pix inválido.")
    if key_type is None:
        if len(options) > 1:
            raise PixKeyError("Formato ambíguo. Selecione CPF ou Telefone.")
        key_type = next(iter(options))
    if key_type not in options:
        raise PixKeyError("A chave informada não corresponde ao tipo selecionado.")
    return key_type, options[key_type]


@contextmanager
def _disk_lock(path: Path):
    """Serialize changes across Python processes on Windows and Linux."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt
            acquired = False
            for _ in range(100):
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.05)
            if not acquired:
                raise PixKeyError("Armazenamento de chaves ocupado. Tente novamente.")
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PixKeyStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_path()

    @contextmanager
    def _locked(self):
        with _LOCK:
            with _disk_lock(self.path):
                yield

    def _read(self) -> dict:
        if self.path.is_symlink():
            raise PixKeyError("Arquivo local de chaves Pix inválido. Preserve-o e faça backup antes de alterar.")
        if not self.path.exists():
            return {"version": 1, "items": []}
        try:
            if self.path.is_symlink():
                raise ValueError("symbolic link")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
                    or not isinstance(data.get("items"), list)
                    or len(data["items"]) > 5000):
                raise ValueError("shape")
            seen_ids, seen_values = set(), set()
            for item in data["items"]:
                if not isinstance(item, dict) or set(item) != {
                    "id", "type", "value", "createdAt", "updatedAt"
                }:
                    raise ValueError("record")
                if not isinstance(item["id"], str) or str(UUID(item["id"])) != item["id"]:
                    raise ValueError("identifier")
                if not isinstance(item["value"], str) or not isinstance(item["type"], str):
                    raise ValueError("value")
                kind, normalized = normalize_key(item["value"], item["type"])
                if normalized != item["value"] or kind != item["type"]:
                    raise ValueError("noncanonical")
                if any(not isinstance(item[key], str) or not item[key]
                       for key in ("createdAt", "updatedAt")):
                    raise ValueError("timestamp")
                if item["id"] in seen_ids or normalized in seen_values:
                    raise ValueError("duplicate")
                seen_ids.add(item["id"])
                seen_values.add(normalized)
            return data
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise PixKeyError("Arquivo local de chaves Pix inválido. Preserve-o e faça backup antes de alterar.") from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".pix-keys-", suffix=".tmp", delete=False
            ) as file:
                name = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
            if os.name != "nt":
                try:
                    fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except OSError:
                    pass
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def list(self) -> dict:
        with self._locked():
            return {"keys": self._read()["items"]}

    def add(self, value: str, key_type: str | None = None) -> dict:
        kind, normalized = normalize_key(value, key_type)
        with self._locked():
            data = self._read()
            if any(p["value"] == normalized for p in data["items"]):
                raise PixKeyError("Esta chave Pix já está cadastrada.")
            if len(data["items"]) >= 5000:
                raise PixKeyError("Limite local de chaves Pix atingido.")
            timestamp = datetime.now(timezone.utc).isoformat()
            record = {"id": str(uuid4()), "type": kind, "value": normalized,
                      "createdAt": timestamp, "updatedAt": timestamp}
            data["items"].append(record)
            self._write(data)
            return {"key": record}

    def update(self, identifier: str, value: str, key_type: str | None = None) -> dict:
        kind, normalized = normalize_key(value, key_type)
        with self._locked():
            data = self._read()
            item = next((r for r in data["items"] if r["id"] == identifier), None)
            if item is None:
                raise PixKeyError("Chave Pix não encontrada.")
            if any(r["id"] != identifier and r["value"] == normalized for r in data["items"]):
                raise PixKeyError("Esta chave Pix já está cadastrada.")
            item["type"], item["value"] = kind, normalized
            item["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._write(data)
            return {"key": item}

    def delete(self, identifier: str) -> dict:
        with self._locked():
            data = self._read()
            remaining = [r for r in data["items"] if r["id"] != identifier]
            if len(remaining) == len(data["items"]):
                raise PixKeyError("Chave Pix não encontrada.")
            data["items"] = remaining
            self._write(data)
            return {"ok": True, "id": identifier}
