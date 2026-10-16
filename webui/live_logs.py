"""In-process live log feed for the paired Nexora panel.

Receives the existing Mãe/Filha UI log messages without persisting account
data, tokens or credentials to a new file. The browser fetches incrementally.
"""
from collections import deque
from datetime import datetime, timezone
import re
import threading

_LOCK = threading.RLock()
_EVENTS = deque(maxlen=50000)
_SEQUENCE = 0
_SECRET = re.compile(
    r"(?i)\b(senha|password|token|authorization|cookie|secret|apikey|api_key)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_URL_AUTH = re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I)
_PERSONAL_NUMBER = re.compile(r"(?<!\d)\d{11}(?!\d)")


def _safe_text(message):
    result = str(message)
    result = _URL_AUTH.sub(r"\1[credenciais-ocultas]@", result)
    result = _SECRET.sub(lambda match: match.group(1) + "=[oculto]", result)
    result = _PERSONAL_NUMBER.sub("[numero-oculto]", result)
    return result


def publish(source: str, message: str) -> None:
    global _SEQUENCE
    # The caller is the existing trusted logger, not an external HTTP input.
    source = source if source in ("Mãe", "Filha", "Sistema") else "Sistema"
    text = _safe_text(message)
    with _LOCK:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            _SEQUENCE += 1
            _EVENTS.append({
                "seq": _SEQUENCE,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
                "text": line[:1200],
            })


def read(after: int = 0, limit: int = 500) -> dict:
    cursor = max(0, int(after))
    page_size = max(1, min(int(limit), 500))
    with _LOCK:
        if not _EVENTS:
            return {"events": [], "cursor": cursor, "hasMore": False,
                    "truncated": False}
        first = _EVENTS[0]["seq"]
        truncated = cursor < first - 1
        start = max(cursor, first - 1)
        events = []
        for item in _EVENTS:
            if item["seq"] > start:
                events.append(dict(item))
                if len(events) >= page_size:
                    break
        next_cursor = events[-1]["seq"] if events else start
        return {"events": events, "cursor": next_cursor,
                "hasMore": next_cursor < _SEQUENCE, "truncated": truncated}
