"""Process-local safety guard for each persistent Chromium account directory."""
from contextlib import contextmanager
import threading

_LOCK = threading.Lock()
_OPEN = set()


class AccountSessionBusy(ValueError):
    pass


@contextmanager
def claim_account(origin: str, account_id: int):
    """Disallow two Chromium processes from opening the same account folder."""
    if origin not in ("mae", "filha") or type(account_id) is not int or account_id < 1:
        raise ValueError("Identificador de conta inválido.")
    key = (origin, account_id)
    with _LOCK:
        if key in _OPEN:
            raise AccountSessionBusy("Esta conta já está aberta em outro navegador.")
        _OPEN.add(key)
    try:
        yield
    finally:
        with _LOCK:
            _OPEN.discard(key)
