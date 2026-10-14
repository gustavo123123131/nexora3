"""Browser dialogs bridging original tkinter.messagebox/simpledialog calls."""
import itertools
import threading

_LOCK = threading.RLock()
_IDS = itertools.count(1)
_PENDING: dict[int, dict] = {}


def request(kind, title, message, initialvalue=None, **kwargs):
    key = next(_IDS)
    item = {
        'id': key, 'kind': kind, 'title': str(title),
        'message': str(message), 'initialvalue': initialvalue,
        'event': threading.Event(), 'answer': None,
    }
    with _LOCK:
        _PENDING[key] = item
    # A dialog needs an active browser. Do not leave a blocked thread indefinitely.
    answered = item['event'].wait(timeout=120)
    with _LOCK:
        _PENDING.pop(key, None)
    if not answered:
        return None if kind == 'askstring' else False
    return item['answer']


def list_pending():
    with _LOCK:
        return [{key: val for key, val in item.items() if key != 'event'} for item in _PENDING.values()]


def answer_dialog(dialog_id, answer):
    with _LOCK:
        item = _PENDING.get(int(dialog_id))
        if item is None:
            return False
        item['answer'] = answer
        item['event'].set()
        return True


def askstring(title, prompt, **kwargs):
    return request('askstring', title, prompt, **kwargs)


def askyesno(title, message, **kwargs):
    return bool(request('askyesno', title, message, **kwargs))


def showwarning(title, message, **kwargs):
    request('showwarning', title, message, **kwargs)


def showinfo(title, message, **kwargs):
    request('showinfo', title, message, **kwargs)


def showerror(title, message, **kwargs):
    request('showerror', title, message, **kwargs)
