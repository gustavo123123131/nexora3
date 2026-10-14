"""Browser entry point for the original Lixo CPA screen classes.

Run: python servidor.py
Original main.py and Settings modules are intentionally not modified.
"""
import argparse
from contextlib import asynccontextmanager
import logging
import os
import uuid
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webui import ctk_compat as ctk, dialogs

ROOT_DIR = Path(__file__).resolve().parent
UI_LOCK = threading.RLock()
_GUI = None
_SERVER_VERSION = uuid.uuid4().hex


def init_original_ui():
    """Import unchanged original GUI modules using the thin CTk web adapter."""
    global _GUI
    with UI_LOCK:
        if _GUI is not None:
            return _GUI
        ctk.reset()
        sys.modules['customtkinter'] = ctk
        # tkinter's dialog modules require a graphical display on Linux; reroute
        # just the dialogs, keeping the original code and its invocation intact.
        import tkinter
        tkinter.messagebox = dialogs
        tkinter.simpledialog = dialogs
        sys.modules['tkinter.messagebox'] = dialogs
        sys.modules['tkinter.simpledialog'] = dialogs
        from main import MainWindow
        _GUI = MainWindow()
        return _GUI


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        if _GUI is not None:
            _GUI.gateway_stop_event.set()
        ctk.shutdown()


app = FastAPI(title='Lixo CPA - localhost', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT_DIR / 'webui' / 'static'), name='static')


def _codespaces_hosts():
    """Only permit the exact forwarded address of this private Codespace."""
    if os.environ.get('CODESPACES') != 'true':
        return set()
    name = os.environ.get('CODESPACE_NAME', '').lower().strip()
    domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev').lower().strip('.')
    if not name or not domain:
        return set()
    base = f'{name}-8765'
    return {f'{base}.{domain}', f'{base}.preview.{domain}'}


@app.middleware('http')
async def only_loopback(request: Request, call_next):
    """Keep local access restricted; Codespaces must use its exact private URL."""
    host = (request.url.hostname or '').lower()
    local = host in ('localhost', '127.0.0.1', '::1', 'testserver')
    cloud = host in _codespaces_hosts()
    if not local and not cloud:
        return JSONResponse({'detail': 'Host não autorizado.'}, status_code=403)
    origin = request.headers.get('origin', '')
    if origin:
        if cloud:
            valid_origins = {f'https://{host}'}
        else:
            port = request.url.port or 80
            valid_origins = {f'http://{h}:{port}' for h in ('localhost', '127.0.0.1')}
        if origin not in valid_origins:
            return JSONResponse({'detail': 'Origem não autorizada.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.get('/')
async def index():
    return FileResponse(ROOT_DIR / 'webui' / 'static' / 'index.html')


@app.get('/api/state')
async def state():
    gui = init_original_ui()
    return {'server_version': _SERVER_VERSION, 'tree': ctk.serialize(gui), 'dialogs': dialogs.list_pending()}


class UiEvent(BaseModel):
    id: str
    event: str
    value: str | bool | float | int | None = None


@app.post('/api/event')
async def event(body: UiEvent):
    init_original_ui()
    w = ctk.get_widget(body.id)
    if not w:
        raise HTTPException(status_code=404, detail='Controle não encontrado')
    if body.event not in ('click', 'change', 'contextmenu'):
        raise HTTPException(status_code=400, detail='Evento inválido')
    if body.event == 'change':
        # Keep the existing Python StringVar/BooleanVar in sync with web controls.
        ctk.execute(body.id, 'change', body.value)
    else:
        # Dialogs and Playwright actions must not block HTTP responses.
        threading.Thread(target=_run_action, args=(body.id, body.event), daemon=True).start()
    return {'ok': True}


def _run_action(widget_id, event_name):
    try:
        ctk.execute(widget_id, event_name)
    except Exception:
        logging.exception('Falha ao executar evento da interface')


class DialogAnswer(BaseModel):
    id: int
    value: str | bool | None = None


@app.post('/api/dialog')
async def answer_dialog(body: DialogAnswer):
    if not dialogs.answer_dialog(body.id, body.value):
        raise HTTPException(status_code=404, detail='Janela expirada')
    return {'ok': True}


def main():
    parser = argparse.ArgumentParser(description='Lixo CPA no navegador (apenas máquina local)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-reload', action='store_true', help='Desliga reload automático de Python/CSS/JS')
    args = parser.parse_args()
    import uvicorn
    # Codespaces forwards a private HTTPS URL to this container's port.
    # The HTTP middleware still rejects other hosts/origins.
    bind_host = '0.0.0.0' if os.environ.get('CODESPACES') == 'true' else '127.0.0.1'
    kwargs = {'host': bind_host, 'port': args.port, 'log_level': 'info'}
    if not args.no_reload:
        # watchfiles reloads the server on original modules and web UI edits.
        kwargs.update(reload=True, reload_dirs=[str(ROOT_DIR)], reload_includes=['*.py', '*.js', '*.css', '*.html'])
    uvicorn.run('servidor:app', **kwargs)


if __name__ == '__main__':
    main()
