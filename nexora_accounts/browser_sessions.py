"""Process-local safety guard and broker for every account-owned Chromium session.

The broker registers the page owner's *worker*, never the Playwright Page.
Commands are consumed on that same worker's thread. No cookies/tokens leave it.
"""
from contextlib import contextmanager
import queue
import threading

_LOCK = threading.Lock()
_OPEN = set()
_ACTIVE = {}


class AccountSessionBusy(ValueError):
    pass


@contextmanager
def claim_account(origin: str, account_id: int, *,
                  account_uuid: str | None = None,
                  stop_event: threading.Event | None = None):
    """Disallow duplicate account folders and register any account-owned worker.

    Works for creation in Mãe/Filha and reopening in Contas. Legacy calls
    without account_uuid retain their original lock-only behavior.
    """
    if origin not in ("mae", "filha") or type(account_id) is not int or account_id < 1:
        raise ValueError("Identificador de conta inválido.")
    if (account_uuid is None) != (stop_event is None):
        raise ValueError("Sessão de conta incompleta.")
    if account_uuid is not None and (
        not isinstance(account_uuid, str) or not account_uuid
        or not isinstance(stop_event, threading.Event)
    ):
        raise ValueError("Identificadores da sessão inválidos.")
    key = (origin, account_id)
    session = None
    with _LOCK:
        if key in _OPEN:
            raise AccountSessionBusy("Esta conta já está aberta em outro navegador.")
        _OPEN.add(key)
        if account_uuid is not None:
            session = {
                "origin": origin,
                "profileId": account_id,
                "accountId": account_uuid,
                "stop": stop_event,
                "ready": False,
                "requests": queue.Queue(maxsize=1),
            }
            _ACTIVE[key] = session
    try:
        yield session
    finally:
        with _LOCK:
            # Identity comparison prevents cleanup of a newer session.
            if session is not None and _ACTIVE.get(key) is session:
                _ACTIVE.pop(key, None)
            _OPEN.discard(key)


def mark_browser_ready(session) -> None:
    """Mark a bound Chromium session ready after browser initialization."""
    if session is None:
        return
    key = (session["origin"], session["profileId"])
    with _LOCK:
        if _ACTIVE.get(key) is session and not session["stop"].is_set():
            session["ready"] = True


def active_account_session(origin: str, profile_id: int, account_uuid: str):
    """Return a queue handle only when the exact account owns the live worker."""
    with _LOCK:
        session = _ACTIVE.get((origin, profile_id))
        if session is None or session["accountId"] != account_uuid:
            return None
        return session


def is_account_open(origin: str, account_id: int) -> bool:
    with _LOCK:
        return (origin, account_id) in _OPEN
