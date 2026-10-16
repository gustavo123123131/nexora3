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
NEXORA_EDITION = 'browser-only'
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
        from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
        ACCOUNT_LAUNCHER.stop_all()
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

def _active_browser_workers():
    # Every open Chromium instance is joined by the owning Mãe/Filha worker.
    # Only read existing widgets; never instantiate a browser to check idle.
    if _GUI is None:
        return 0
    count = 0
    pending = [_GUI]
    while pending:
        current = pending.pop()
        jobs = getattr(current, "_browser_jobs", None)
        if jobs is not None:
            count += sum(1 for thread, _ in list(jobs) if thread.is_alive())
        else:
            worker = getattr(current, "worker_thread", None)
            if worker is not None and worker.is_alive():
                count += 1
        pending.extend(getattr(current, "children", ()))
    return count


@app.get('/api/update/idle')
async def update_idle():
    # Used only by the local supervisor to avoid interrupting open browsers.
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
    workers = _active_browser_workers() + ACCOUNT_LAUNCHER.active_count()
    return {"edition": NEXORA_EDITION, "idle": workers == 0,
            "activeWorkers": workers}


@app.get('/api/health')
async def health():
    """Lightweight liveness check; does not initialize accounts or GUI."""
    return {'service': 'lixocpa', 'status': 'ok', 'edition': NEXORA_EDITION, 'server_version': _SERVER_VERSION}


@app.get('/')
async def index():
    return FileResponse(ROOT_DIR / 'webui' / 'static' / 'index.html')


@app.get('/api/live-logs')
async def live_logs(after: int = 0, limit: int = 500):
    """Incremental application logs, protected by the existing agent pairing."""
    from webui.live_logs import read
    return read(after=after, limit=limit)


@app.get('/api/state')
async def state():
    gui = init_original_ui()
    return {'server_version': _SERVER_VERSION, 'edition': NEXORA_EDITION, 'csrf_token': _CSRF_TOKEN, 'tree': ctk.serialize(gui), 'dialogs': dialogs.list_pending()}


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



# ProxyBet-style proxy workspace: local CRUD, selection and TCP diagnostics.
# The existing agent-key + CSRF middleware protects these endpoints.
# Secrets are never returned by GET /api/proxies.
def _proxy_request(callback):
    from nexora_accounts.proxy_store import ProxyError
    try:
        return callback()
    except ProxyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/proxies')
async def proxy_list():
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().list())


@app.post('/api/proxies/save')
async def proxy_save(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return {'proxy': _proxy_request(lambda: ProxyStore().save(body))}


@app.post('/api/proxies/add-line')
async def proxy_add_line(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return {'proxy': _proxy_request(lambda: ProxyStore().save_line(body.get('line')))}


@app.post('/api/proxies/delete')
async def proxy_delete(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().delete(body.get('id')))


@app.post('/api/proxies/activate')
async def proxy_activate(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().activate(body.get('id')))


@app.post('/api/proxies/test')
async def proxy_test(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().test(body.get('id')))


@app.get('/api/proxies/profile-bindings')
async def proxy_profile_bindings():
    from nexora_accounts.proxy_store import ProxyStore
    return {'bindings': _proxy_request(lambda: ProxyStore().profile_bindings())}


@app.post('/api/proxies/bind-profile')
async def proxy_bind_profile(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().bind_profile(body.get('profileId'), body.get('proxyId')))


@app.post('/api/proxies/check')
async def proxy_check(body: dict):
    from nexora_accounts.proxy_store import ProxyStore
    return _proxy_request(lambda: ProxyStore().test_authenticated(body.get('id')))



# Local Pix key management. Uses existing origin, agent-key and CSRF middleware.
# No bank lookups, Pix transfers or external calls.
def _pix_action(operation):
    from nexora_accounts.pix_key_store import PixKeyError
    try:
        return operation()
    except PixKeyError as exc:
        message = str(exc)
        code = 409 if "já está cadastrada" in message else 422
        raise HTTPException(status_code=code, detail=message) from exc


@app.get('/api/pix-keys')
async def list_pix_keys():
    from nexora_accounts.pix_key_store import PixKeyStore
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(lambda: _pix_action(PixKeyStore().list))


@app.post('/api/pix-keys/add')
async def add_pix_key(body: dict):
    from nexora_accounts.pix_key_store import PixKeyStore
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(
        lambda: _pix_action(lambda: PixKeyStore().add(
            body.get("value"), body.get("type")
        ))
    )


@app.post('/api/pix-keys/update')
async def update_pix_key(body: dict):
    from nexora_accounts.pix_key_store import PixKeyStore
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(
        lambda: _pix_action(lambda: PixKeyStore().update(
            body.get("id"), body.get("value"), body.get("type")
        ))
    )


@app.post('/api/pix-keys/delete')
async def delete_pix_key(body: dict):
    from nexora_accounts.pix_key_store import PixKeyStore
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(
        lambda: _pix_action(lambda: PixKeyStore().delete(body.get("id")))
    )


@app.get('/api/local-profiles')
async def list_local_profiles():
    from nexora_accounts.local_profiles import LocalProfileStore
    store = LocalProfileStore()
    return {'profiles': store.public_list(), 'selectedProfileId': store.selected_id()}


@app.post('/api/local-profiles/select')
async def select_local_profile(body: dict):
    from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
    try:
        selected_id = LocalProfileStore().select(body.get('id'))
        return {'selectedProfileId': selected_id}
    except LocalProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/local-profiles/integrity')
async def local_profile_integrity():
    from nexora_accounts.local_profiles import LocalProfileStore
    return LocalProfileStore().integrity_report()


@app.get('/api/local-profiles/recovery')
async def local_profile_recovery_candidates():
    from nexora_accounts.local_profiles import LocalProfileStore
    return {"candidates": LocalProfileStore().recovery_candidates()}


@app.post('/api/local-profiles/restore')
async def local_profile_restore(body: dict):
    from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
    if body.get("confirmed") is not True:
        raise HTTPException(status_code=422, detail="Confirme a restauração dos metadados.")
    try:
        return LocalProfileStore().restore_account_from_backup(body.get("accountId"))
    except LocalProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/local-profiles/detail')
async def local_profile_details(id: int):
    from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
    try:
        return LocalProfileStore().public_detail(id)
    except LocalProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/api/local-profiles/open')
async def open_local_profile(body: dict):
    from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER, AccountBusyError
    try:
        # Identity comes exclusively from the card's ID, not a global
        # selectedProfileId or the current Mãe/Filha tab.
        profile = LocalProfileStore().get(body.get('id'))
        if not profile.get("runtimeConfig"):
            raise LocalProfileError(
                "Configuração original de dispositivo indisponível nesta conta. "
                "Não é possível reabrir com dados de outro dispositivo."
            )
        return ACCOUNT_LAUNCHER.launch(init_original_ui(), profile["id"])
    except AccountBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LocalProfileError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@app.get('/api/local-profiles/open-state')
async def open_local_profile_state(id: int):
    from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
    try:
        LocalProfileStore().get(id)
    except LocalProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ACCOUNT_LAUNCHER.state(id)


@app.post('/api/local-profiles/close')
async def close_local_profile(body: dict):
    from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
    try:
        LocalProfileStore().get(body.get('id'))
        return ACCOUNT_LAUNCHER.close(body.get('id'))
    except LocalProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/account-backups')
async def list_account_backups():
    from nexora_accounts.account_backups import list_snapshots
    return {"backups": list_snapshots()}


@app.get('/api/account-backups/detail')
async def account_backup_detail(backupId: str):
    from nexora_accounts.local_profiles import LocalProfileError
    from nexora_accounts.account_backups import snapshot_detail
    try:
        return snapshot_detail(backupId)
    except LocalProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/api/account-backups/restore')
async def restore_account_backup(body: dict):
    from nexora_accounts.local_profiles import LocalProfileError
    from nexora_accounts.account_backups import restore_snapshot
    if body.get("confirmed") is not True:
        raise HTTPException(status_code=422, detail="Confirme a restauração.")
    try:
        return restore_snapshot(body.get("backupId"))
    except LocalProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post('/api/local-profiles/delete-batch')
async def delete_selected_accounts(body: dict):
    from nexora_accounts.local_profiles import LocalProfileError
    from nexora_accounts.browser_sessions import AccountSessionBusy
    from nexora_accounts.profile_cleanup import delete_account
    ids = body.get("ids")
    if body.get("confirmed") is not True or not isinstance(ids, list) or not 1 <= len(ids) <= 200:
        raise HTTPException(status_code=422, detail="Selecione e confirme de 1 a 200 contas.")
    if any(type(value) is not int or value < 1 for value in ids) or len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="IDs de contas inválidos ou repetidos.")
    from starlette.concurrency import run_in_threadpool
    deleted, errors = [], []
    for profile_id in ids:
        try:
            # Preserve per-account ordering and backups, but do not freeze
            # other agent requests while a large Chromium profile is copied.
            result = await run_in_threadpool(delete_account, profile_id)
            deleted.append(result)
        except (LocalProfileError, AccountSessionBusy) as exc:
            errors.append({"id": profile_id, "reason": str(exc)})
        except OSError:
            errors.append({"id": profile_id, "reason": "Falha ao salvar backup ou excluir arquivos."})
    return {"deleted": deleted, "errors": errors}


@app.post('/api/local-profiles/delete')
async def delete_local_profile(body: dict):
    from nexora_accounts.local_profiles import LocalProfileError
    from nexora_accounts.browser_sessions import AccountSessionBusy
    from nexora_accounts.profile_cleanup import delete_account
    if body.get('confirmed') is not True:
        raise HTTPException(status_code=422, detail="Confirme a exclusão da conta.")
    try:
        remove_files = body.get('removeFiles', True)
        if type(remove_files) is not bool:
            raise LocalProfileError("Opção de exclusão inválida.")
        # Full Chromium/DPAPI backup can take seconds or minutes. Never
        # block the FastAPI event loop while files are copied/deleted.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(
            delete_account, body.get('id'), remove_files=remove_files
        )
    except AccountSessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LocalProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



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
