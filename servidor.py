"""Browser entry point for the original Lixo CPA screen classes.

Run: python servidor.py
Original main.py and Settings modules are intentionally not modified.
"""
import argparse
from contextlib import asynccontextmanager
import logging
import os
import secrets
import uuid
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webui import ctk_compat as ctk, dialogs

ROOT_DIR = Path(__file__).resolve().parent
UI_LOCK = threading.RLock()
_GUI = None
_SERVER_VERSION = uuid.uuid4().hex
_CSRF_TOKEN = secrets.token_urlsafe(32)


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
        if _accounts_service is not None:
            _accounts_service.stop()
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


def _agent_allowed_origin():
    return os.environ.get("LIXOCPA_AGENT_ORIGIN", "").rstrip("/")


def _cors_agent_response(response, origin):
    if origin == _agent_allowed_origin() and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-LixoCPA-CSRF, X-LixoCPA-Agent-Key"
        )
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Access-Control-Max-Age"] = "300"
        response.headers["Vary"] = "Origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def only_loopback(request: Request, call_next):
    """Protect the local agent while allowing a paired private Codespaces panel.

    The GitHub proxy can rewrite Origin, so the CLOUD panel uses CSRF tokens.
    On Windows only an explicitly paired Codespaces Origin gets CORS access;
    actions additionally require an unguessable key and CSRF protection.
    """
    host = (request.url.hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1", "testserver")
    cloud = host in _codespaces_hosts()
    agent_origin = _agent_allowed_origin()
    origin = request.headers.get("origin", "")
    agent_mode = bool(agent_origin)

    if not local and not cloud:
        return JSONResponse({"detail": "Host não autorizado."}, status_code=403)

    if agent_mode:
        # Pairing is not a public API; no wildcard origins, no LAN listening.
        local_origins = {
            f"http://localhost:{request.url.port or 8767}",
            f"http://127.0.0.1:{request.url.port or 8767}",
        }
        if origin and origin != agent_origin and origin not in local_origins:
            return JSONResponse({"detail": "Origem não autorizada no agente."}, status_code=403)
        if request.method == "OPTIONS":
            if origin != agent_origin:
                return JSONResponse({"detail": "Origem não autorizada."}, status_code=403)
            from fastapi.responses import Response
            return _cors_agent_response(Response(status_code=204), origin)
        if origin == agent_origin and request.url.path.startswith("/api/"):
            presented_key = request.headers.get("x-lixocpa-agent-key", "")
            expected_key = os.environ.get("LIXOCPA_AGENT_KEY", "")
            if not expected_key or not secrets.compare_digest(presented_key, expected_key):
                return _cors_agent_response(
                    JSONResponse({"detail": "Código de pareamento inválido."}, status_code=403),
                    origin,
                )
    elif os.environ.get("CODESPACES") == "true" and request.method == "POST" and request.url.path.startswith("/api/"):
        # Never open Chromium in the Codespaces container when the user wants
        # Windows-local execution. The static cloud panel connects to the agent.
        return JSONResponse(
            {"detail": "Use o agente Windows para executar comandos pelo painel."},
            status_code=403,
        )

    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        presented = request.headers.get("x-lixocpa-csrf", "")
        if not presented or not secrets.compare_digest(presented, _CSRF_TOKEN):
            return _cors_agent_response(
                JSONResponse(
                    {"detail": "Sessão de segurança inválida. Atualize a página."},
                    status_code=403,
                ),
                origin if agent_mode else "",
            )

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if agent_mode:
        _cors_agent_response(response, origin)
    return response

@app.get('/api/health')
async def health():
    """Lightweight liveness check; does not initialize accounts or GUI."""
    return {'service': 'lixocpa', 'status': 'ok', 'server_version': _SERVER_VERSION}


@app.get('/')
async def index():
    return FileResponse(ROOT_DIR / 'webui' / 'static' / 'index.html')


@app.get('/api/state')
async def state():
    gui = init_original_ui()
    return {'server_version': _SERVER_VERSION, 'csrf_token': _CSRF_TOKEN, 'tree': ctk.serialize(gui), 'dialogs': dialogs.list_pending()}


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



# Cadastro e diretório de contas: executados SOMENTE pelo agente localhost do
# Windows. A página do Codespaces acessa estas rotas via pareamento privado.
_accounts_service = None


def _accounts():
    global _accounts_service
    if _accounts_service is None:
        from nexora_accounts.service import AccountsService
        _accounts_service = AccountsService()
    return _accounts_service


def _account_request(callable_):
    from nexora_accounts.service import AccountError
    try:
        return callable_()
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/registration/sites')
async def registration_sites():
    return {'sites': _accounts().repo.sites()}


@app.post('/api/registration/sites')
async def registration_add_site(body: dict):
    return {'site': _account_request(lambda: _accounts().repo.add_site(body))}


@app.get('/api/registration/accounts')
async def registration_accounts(query: str = '', status: str = ''):
    return {'accounts': _account_request(lambda: _accounts().repo.accounts(query, status))}


@app.post('/api/registration/accounts')
async def registration_add_account(body: dict):
    quantity = body.get('quantity', 1)
    if quantity == 1:
        return {'account': _account_request(lambda: _accounts().repo.add_account(body)), 'quantity': 1}
    return _account_request(lambda: _accounts().repo.create_batch(body))


@app.post('/api/registration/accounts/delete')
async def registration_delete_account(body: dict):
    _account_request(lambda: _accounts().repo.delete(body.get('accountId')))
    return {'ok': True}


async def _registration_browser(action: str, body: dict):
    import asyncio
    from nexora_accounts.service import AccountError
    key = body.get('accountId', '')
    if not isinstance(key, str) or len(key) > 60:
        raise HTTPException(status_code=422, detail='Conta inválida.')
    service = _accounts()
    try:
        account = service.repo.account(key)
        if action != 'open' and not service.repo.site(account['siteId'])['authorized']:
            raise AccountError('O cadastro guiado exige autorização explícita do site.')
        if service.browser is None:
            raise HTTPException(status_code=503, detail='Navegador local não inicializado.')
        future = service.browser.execute(action, key, body)
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=35)
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail='Operação demorou demais. Confira o navegador.') from exc


@app.post('/api/registration/accounts/open')
async def registration_open_account(body: dict):
    return await _registration_browser('open', body)


@app.post('/api/registration/prepare')
async def registration_prepare(body: dict):
    return await _registration_browser('prepare', body)


@app.post('/api/registration/submit')
async def registration_submit(body: dict):
    return await _registration_browser('submit', body)


@app.post('/api/registration/refresh')
async def registration_refresh(body: dict):
    return await _registration_browser('refresh', body)

def main():
    parser = argparse.ArgumentParser(description='Lixo CPA no navegador (apenas máquina local)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-reload', action='store_true', help='Desliga reload automático de Python/CSS/JS')
    args = parser.parse_args()
    import uvicorn
    # Codespaces forwards a private HTTPS URL to this container's port.
    # The HTTP middleware restricts hosts and requires CSRF tokens on commands.
    bind_host = '0.0.0.0' if os.environ.get('CODESPACES') == 'true' else '127.0.0.1'
    kwargs = {'host': bind_host, 'port': args.port, 'log_level': 'info'}
    if not args.no_reload:
        # watchfiles reloads the server on original modules and web UI edits.
        kwargs.update(reload=True, reload_dirs=[str(ROOT_DIR)], reload_includes=['*.py', '*.js', '*.css', '*.html'])
    uvicorn.run('servidor:app', **kwargs)


if __name__ == '__main__':
    main()
