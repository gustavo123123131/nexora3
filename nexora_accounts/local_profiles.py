"""Generate and persist demonstration profiles entirely on the local Nexora agent.

No browser, HTTP client, external form or account-registration integration is used.
Names, contact details and document numbers are synthetic and not verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import string
import tempfile
import threading
import unicodedata

_LOCK = threading.RLock()
_FIRST_NAMES = (
    "Ana", "Beatriz", "Bianca", "Bruna", "Camila", "Carolina",
    "Daniel", "Eduardo", "Felipe", "Gabriel", "Helena", "Isabela",
    "João", "Julia", "Larissa", "Lucas", "Mariana", "Mateus",
    "Nicolas", "Patricia", "Pedro", "Rafael", "Renata", "Sofia",
    "Thiago", "Vitoria",
)
_MIDDLE_NAMES = (
    "Alice", "Cristina", "Eduarda", "Fernanda", "Gabriela",
    "Henrique", "Luiza", "Maria", "Miguel", "Oliveira",
)
_LAST_NAMES = (
    "Almeida", "Barbosa", "Cardoso", "Carvalho", "Costa",
    "Ferreira", "Gomes", "Lima", "Martins", "Mendes",
    "Moreira", "Oliveira", "Pereira", "Ribeiro", "Rocha",
    "Santos", "Silva", "Souza",
)
_DDDS = ("11", "12", "13", "21", "22", "27", "31", "32", "41", "47", "51", "61", "62", "71", "81", "85")


class LocalProfileError(ValueError):
    """Safe error displayed to the operator, never containing credentials."""


def _default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    directory = Path(base) if base else Path.home() / ".local" / "share"
    return directory / "Nexora" / "generated_profiles.json"


def _digits(n: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(n))


def _new_cpf() -> str:
    """Generate eleven checksum-consistent digits, not a verified identity."""
    while True:
        digits = [secrets.randbelow(10) for _ in range(9)]
        if len(set(digits)) == 1:
            continue
        for size in (9, 10):
            check = (sum(digits[i] * (size + 1 - i) for i in range(size)) * 10) % 11 % 10
            digits.append(check)
        value = "".join(map(str, digits))
        return f"{value[:3]}.{value[3:6]}.{value[6:9]}-{value[9:]}"


def _new_password() -> str:
    symbols = "#@!$%"
    alphabet = string.ascii_letters + string.digits + symbols
    chars = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    chars.extend(secrets.choice(alphabet) for _ in range(5))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _username(full_name: str, existing: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", full_name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c)).lower()
    pieces = ["".join(c for c in p if c in string.ascii_lowercase) for p in normalized.split()]
    stem = ((pieces[0] if pieces else "usuario") + (pieces[-1] if pieces else ""))[:8]
    if len(stem) < 5:
        stem = (stem + "perfil")[:8]
    for _ in range(1000):
        name = stem + _digits(2)
        if 7 <= len(name) <= 10 and name not in existing:
            return name
    raise LocalProfileError("Não foi possível criar um username exclusivo.")


class LocalProfileStore:
    """Small append-only JSON repository; the file stays on the Windows agent."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_path()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"nextNumber": 1, "profiles": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("profiles"), list)
                or not isinstance(data.get("nextNumber"), int)
                or data["nextNumber"] < 1
            ):
                raise ValueError("invalid profile file")
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise LocalProfileError(
                "O arquivo local de perfis está inválido. Faça backup antes de alterar."
            ) from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".profiles-", suffix=".tmp", delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def list(self) -> list[dict]:
        with _LOCK:
            return [p.copy() for p in self._read()["profiles"]]

    def create(self, origin: str) -> dict:
        if origin not in ("mae", "filha"):
            raise LocalProfileError("Origem do perfil inválida.")
        with _LOCK:
            data = self._read()
            number = data["nextNumber"]
            first = secrets.choice(_FIRST_NAMES)
            surname = secrets.choice(_LAST_NAMES)
            parts = [first]
            if secrets.randbelow(2):
                parts.append(secrets.choice(_MIDDLE_NAMES))
            parts.append(surname)
            full_name = " ".join(parts)
            username = _username(
                full_name,
                {str(p.get("username", "")) for p in data["profiles"] if isinstance(p, dict)},
            )
            password = _new_password()
            ddd = secrets.choice(_DDDS)
            phone = "9" + _digits(8)
            record = {
                "id": number,
                "label": f"Conta {number}",
                "origin": origin,
                "fullName": full_name,
                "username": username,
                "email": f"{username}@gmail.com",
                "phone": f"({ddd}) {phone[:5]}-{phone[5:]}",
                "cpf": _new_cpf(),
                "password": password,
                "confirmPassword": password,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            data["profiles"].append(record)
            data["nextNumber"] = number + 1
            self._write(data)
            return record.copy()
