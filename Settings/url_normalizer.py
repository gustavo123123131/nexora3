"""Normalização compartilhada das URLs usadas por Criar Mãe e Criar Filha.

Somente endereços HTTP(S) são navegáveis pelo Chromium. Quando o usuário
omite o protocolo, HTTPS é o padrão; protocolos explícitos são preservados.
Não realiza requisições nem altera perfis ou configurações de rede.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Informe um domínio ou uma URL válida.")

    # Evita a interpretação de 'http:exemplo.com' como domínio.
    if _SCHEME.match(raw):
        if not re.match(r"^https?://", raw, re.IGNORECASE):
            raise ValueError(
                f"Protocolo não suportado em '{raw}'. Use HTTP ou HTTPS explicitamente."
            )
        url = raw
    else:
        if raw.startswith(("/", "\\", "?")) or "://" in raw:
            raise ValueError(f"Endereço inválido: '{raw}'.")
        url = "https://" + raw

    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError(f"A URL contém espaços ou caracteres inválidos: '{raw}'.")

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port  # força validação de porta numérica e limites
    except ValueError as exc:
        raise ValueError(f"URL inválida: '{raw}'.") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Somente URLs HTTP ou HTTPS são aceitas.")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError(f"Domínio inválido ou credenciais embutidas na URL: '{raw}'.")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"Porta inválida em '{raw}'.")

    if hostname != "localhost":
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            try:
                ascii_host = hostname.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(f"Domínio inválido: '{raw}'.") from exc
            labels = ascii_host.split(".")
            if (
                len(ascii_host) > 253
                or len(labels) < 2
                or any(not _HOST_LABEL.fullmatch(label) for label in labels)
                or labels[-1].isdigit()
            ):
                raise ValueError(f"Domínio inválido: '{raw}'.")

    # Retorna a URL original (sem espaços nas pontas), preservando protocolo
    # explícito, subdomínio, porta, caminho, consulta e fragmento.
    return url


def normalize_url_list(value: str) -> list[str]:
    raw_values = [item.strip() for item in str(value or "").split(",")]
    if not any(raw_values):
        raise ValueError("Informe pelo menos um domínio ou URL.")
    if any(not item for item in raw_values):
        raise ValueError("A lista contém uma URL vazia. Separe os links por vírgulas.")
    return [normalize_url(item) for item in raw_values]


def is_https_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() == "https"


class HttpsNavigationError(RuntimeError):
    """Navegação HTTPS falhou: não repetir automaticamente com a mesma URL."""
