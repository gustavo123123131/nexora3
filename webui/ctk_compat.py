"""Small server-side CustomTkinter-compatible widget model.

Keeps the original screen classes and callbacks intact; browser only renders
these controls. This is NOT a complete CustomTkinter implementation.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any

_LOCK = threading.RLock()
_IDS = itertools.count(1)
_REGISTRY: dict[str, 'Widget'] = {}
_TIMERS: set[threading.Timer] = set()
_CLOSED = False


def reset():
    global _IDS, _CLOSED
    with _LOCK:
        for timer in list(_TIMERS):
            timer.cancel()
        _TIMERS.clear()
        _REGISTRY.clear()
        _IDS = itertools.count(1)
        _CLOSED = False


def shutdown():
    global _CLOSED
    with _LOCK:
        _CLOSED = True
        for timer in list(_TIMERS):
            timer.cancel()
        _TIMERS.clear()


def get_widget(widget_id: str) -> 'Widget | None':
    with _LOCK:
        return _REGISTRY.get(str(widget_id))


class Variable:
    def __init__(self, master=None, value=None, **kwargs):
        self._value = value

    def get(self):
        with _LOCK:
            return self._value

    def set(self, value):
        with _LOCK:
            self._value = value


class StringVar(Variable):
    def __init__(self, master=None, value='', **kwargs):
        super().__init__(master, value, **kwargs)

    def set(self, value):
        super().set(str(value))


class BooleanVar(Variable):
    def __init__(self, master=None, value=False, **kwargs):
        super().__init__(master, bool(value), **kwargs)

    def set(self, value):
        super().set(bool(value))


class IntVar(Variable):
    def __init__(self, master=None, value=0, **kwargs):
        super().__init__(master, int(value), **kwargs)

    def set(self, value):
        super().set(int(value))


class DoubleVar(Variable):
    def __init__(self, master=None, value=0.0, **kwargs):
        super().__init__(master, float(value), **kwargs)


def CTkFont(*, family=None, size=13, weight='normal', **kwargs):
    return {'family': family, 'size': size, 'weight': weight}


def set_appearance_mode(mode):
    pass


def set_default_color_theme(theme):
    pass


class Widget:
    widget_type = 'frame'

    def __init__(self, master=None, **options):
        with _LOCK:
            self.id = str(next(_IDS))
            self.master = master
            self.children: list[Widget] = []
            self.options = dict(options)
            self.layout = {'method': 'pack', 'fill': 'none'}
            self.hidden = False
            self.cols = {}
            self.rows = {}
            self.bindings = {}
            self._text = ''
            self._destroyed = False
            if isinstance(master, Widget):
                master.children.append(self)
            _REGISTRY[self.id] = self

    def pack(self, **kwargs):
        with _LOCK:
            self.layout = {'method': 'pack', **kwargs}
            self.hidden = False
        return None

    def grid(self, **kwargs):
        with _LOCK:
            if kwargs:
                self.layout = {'method': 'grid', **kwargs}
            self.hidden = False
        return None

    def grid_remove(self):
        self.hidden = True

    def grid_forget(self):
        self.hidden = True

    def pack_forget(self):
        self.hidden = True

    def grid_columnconfigure(self, index, weight=0, **kwargs):
        with _LOCK:
            for i in index if isinstance(index, (tuple, list)) else [index]:
                self.cols[str(i)] = weight

    def grid_rowconfigure(self, index, weight=0, **kwargs):
        with _LOCK:
            for i in index if isinstance(index, (tuple, list)) else [index]:
                self.rows[str(i)] = weight

    def configure(self, **kwargs):
        with _LOCK:
            self.options.update(kwargs)

    config = configure

    def cget(self, option):
        return self.options.get(option)

    def get(self):
        var = self.options.get('variable') or self.options.get('textvariable')
        return var.get() if isinstance(var, Variable) else self._text

    def set(self, value):
        var = self.options.get('variable') or self.options.get('textvariable')
        if isinstance(var, Variable):
            var.set(value)
        else:
            self._text = str(value)

    def insert(self, index, text):
        with _LOCK:
            if index in ('end', 'END'):
                self._text += str(text)
            else:
                self._text = str(text) + self._text

    def delete(self, first, last=None):
        with _LOCK:
            self._text = ''

    def see(self, index):
        pass

    def bind(self, event, handler, add=None):
        with _LOCK:
            self.bindings[str(event)] = handler

    def after(self, milliseconds: int, func=None, *args):
        if func is None:
            return None
        timer = None

        def fire():
            with _LOCK:
                _TIMERS.discard(timer)
                if _CLOSED or self._destroyed:
                    return
            try:
                func(*args)
            except Exception:
                import traceback
                traceback.print_exc()

        timer = threading.Timer(max(0, int(milliseconds)) / 1000.0, fire)
        timer.daemon = True
        with _LOCK:
            if _CLOSED:
                return None
            _TIMERS.add(timer)
            timer.start()
        return timer

    def after_cancel(self, token):
        if token:
            token.cancel()
            with _LOCK:
                _TIMERS.discard(token)

    def winfo_toplevel(self):
        obj = self
        while isinstance(obj.master, Widget):
            obj = obj.master
        return obj

    def winfo_exists(self):
        return not self._destroyed

    def destroy(self):
        with _LOCK:
            self._destroyed = True
            for child in list(self.children):
                child.destroy()
            self.children.clear()
            if isinstance(self.master, Widget):
                if self in self.master.children:
                    self.master.children.remove(self)
            _REGISTRY.pop(self.id, None)

    def title(self, value=None):
        if value is not None:
            self.options['title'] = value
        return self.options.get('title', '')

    def geometry(self, value):
        self.options['geometry'] = value

    def minsize(self, width, height):
        self.options['minsize'] = [width, height]

    def protocol(self, protocol, callback):
        self.options.setdefault('_protocols', {})[protocol] = callback

    def transient(self, parent):
        pass

    def grab_set(self):
        pass

    def focus(self):
        pass

    def focus_force(self):
        pass

    def lift(self):
        pass

    def mainloop(self):
        raise RuntimeError('Esta interface roda em servidor.py no navegador, não via mainloop().')


class CTk(Widget):
    widget_type = 'root'


class CTkFrame(Widget):
    widget_type = 'frame'


class CTkScrollableFrame(Widget):
    widget_type = 'scroll'


class CTkLabel(Widget):
    widget_type = 'label'


class CTkEntry(Widget):
    widget_type = 'entry'


class CTkButton(Widget):
    widget_type = 'button'


class CTkSwitch(Widget):
    widget_type = 'switch'


class CTkCheckBox(Widget):
    widget_type = 'checkbox'


class CTkOptionMenu(Widget):
    widget_type = 'option'


class CTkTextbox(Widget):
    widget_type = 'textbox'


class CTkToplevel(Widget):
    widget_type = 'modal'


class CTkTabview(Widget):
    widget_type = 'tabs'

    def add(self, name):
        tab = CTkFrame(self, _tab_label=str(name))
        tab.pack(fill='both', expand=True)
        return tab


def execute(widget_id: str, event: str, value: Any = None):
    widget = get_widget(widget_id)
    if widget is None or widget._destroyed:
        raise ValueError('Controle não encontrado')
    if widget.options.get('state') == 'disabled':
        return
    if event == 'change':
        variable = widget.options.get('variable') or widget.options.get('textvariable')
        if isinstance(variable, BooleanVar):
            variable.set(bool(value))
        elif isinstance(variable, Variable):
            variable.set(value)
        else:
            widget.set(value)
        callback = widget.options.get('command')
        if callback and widget.widget_type in ('option', 'checkbox', 'switch'):
            if widget.widget_type == 'option':
                callback(value)
            else:
                callback()
    elif event == 'click':
        callback = widget.options.get('command')
        if callable(callback):
            callback()
    elif event == 'contextmenu':
        callback = widget.bindings.get('<Button-3>')
        if callable(callback):
            callback(None)
    else:
        raise ValueError('Evento desconhecido')


def serialize(widget: Widget) -> dict:
    """Serialize only rendering information, never Python callbacks or internals."""
    with _LOCK:
        options = {}
        for key, val in widget.options.items():
            if key in ('command', '_protocols'):
                continue
            if isinstance(val, Variable):
                options[key] = val.get()
            elif isinstance(val, (str, int, float, bool, list, dict)) or val is None:
                options[key] = val
        if 'textvariable' in widget.options:
            options['text'] = widget.options['textvariable'].get()
        if 'variable' in widget.options:
            options['value'] = widget.options['variable'].get()
        if widget.widget_type in ('entry', 'option'):
            options['value'] = widget.get()
        if widget.widget_type == 'textbox':
            # The consolidated live feed transports its log text separately.
            # Preserve ordinary textboxes; only tagged diagnostic buffers
            # omit their duplicate contents from high-frequency UI snapshots.
            options['value'] = (
                '' if getattr(widget, '_nexora_logs_live_only', False)
                else widget._text
            )
        return {
            'id': widget.id,
            'type': widget.widget_type,
            'options': options,
            'layout': widget.layout,
            'hidden': widget.hidden,
            'cols': widget.cols,
            'rows': widget.rows,
            'children': [serialize(child) for child in list(widget.children) if not child._destroyed],
        }
