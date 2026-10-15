"""Short, structured registration diagnostics shared by Mãe and Filha.

The logging surface only includes stage IDs, internal profile reference,
elapsed milliseconds and a sanitized error category. No form inputs/URLs.
"""
from datetime import datetime, timezone
import logging
import re
import threading
import time

from nexora_accounts.service import AccountsRepository

_CLOCK = {}
_LOCK = threading.Lock()


def trace_registration(account_id, profile_index, stage, status="local",
                       ui_log=None, error_code=None, repository=None):
    if not isinstance(account_id, str) or not account_id:
        return None
    if not re.fullmatch(r"[a-z0-9_-]{1,60}", stage):
        raise ValueError("Etapa inválida.")
    if error_code and not re.fullmatch(r"[a-z0-9_-]{1,60}", error_code):
        raise ValueError("Código de erro inválido.")
    now = time.monotonic()
    with _LOCK:
        prev = _CLOCK.get(account_id, now)
        _CLOCK[account_id] = now
    duration_ms = max(0, min(int((now - prev) * 1000), 86_400_000))
    repo = repository if repository is not None else AccountsRepository()
    repo.set_stage(
        account_id, status, stage, error_code=error_code, duration_ms=duration_ms
    )
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = ("[{at}] [perfil #{index} id={id}] [{stage}] {time} ms"
            "{error}").format(
        at=timestamp, index=profile_index, id=account_id[:8],
        stage=stage, time=duration_ms,
        error=" erro=" + error_code if error_code else "",
    )
    logging.getLogger("nexora.registration").info(line)
    print(line, flush=True)  # Windows-agent terminal / supervisor logs
    if ui_log:
        ui_log(line)
    return line
