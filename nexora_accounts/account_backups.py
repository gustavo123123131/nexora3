"""Snapshots of deleted account metadata and original Chromium user-data.

A snapshot must be COMPLETE before an account can be deleted. On Windows,
each file and the sensitive manifest are encrypted using current-user DPAPI.
The backup index only exposes minimal non-secret display metadata.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import uuid

from nexora_accounts import credential_vault
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from Settings.device_modes import stored_profile_directory


def _root(store):
    return store.path.parent / "deleted-account-backups"


def _private_write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _safe_relative(name):
    raw = PurePosixPath(name)
    if not name or raw.is_absolute() or any(part in ("..", ".", "") for part in raw.parts) or "\\" in name:
        raise LocalProfileError("Caminho inválido dentro do backup.")
    return Path(*raw.parts)


def _seal(payload):
    return credential_vault._crypt(payload) if credential_vault.is_available() else payload


def _unseal(payload, *, protected):
    return credential_vault._crypt(payload, decrypt=True) if protected else payload


def _read_manifest(snapshot):
    meta = json.loads((snapshot / "index.json").read_text(encoding="utf-8"))
    payload = (snapshot / "payload.bin").read_bytes()
    record = json.loads(_unseal(payload, protected=meta["encrypted"]).decode("utf-8"))
    return meta, record


def create_snapshot(profile_id, *, store=None):
    """Snapshot one CLOSED account; never copy a profile being written to."""
    store = store or LocalProfileStore()
    if type(profile_id) is not int:
        raise LocalProfileError("ID da conta inválido.")
    profile = store.get(profile_id)
    raw = next((p.copy() for p in store.list() if p["id"] == profile_id), None)
    if raw is None:
        raise LocalProfileError("Conta não encontrada.")
    from Settings.Criar_Mae.settings.utils import PROFILES_DIR as MAE_DIR
    from Settings.Criar_Filha.settings.utils import PROFILES_DIR as FILHA_DIR
    base = MAE_DIR if profile["origin"] == "mae" else FILHA_DIR
    try:
        original = stored_profile_directory(base, profile)
    except ValueError as exc:
        raise LocalProfileError("Pasta original da conta inválida; exclusão interrompida.") from exc
    backup_root = _root(store)
    backup_root.mkdir(parents=True, exist_ok=True)
    try:
        backup_root.chmod(0o700)
    except OSError:
        pass
    snapshot_id = str(uuid.uuid4())
    staging = Path(tempfile.mkdtemp(prefix=".account-incomplete-", dir=backup_root))
    final = backup_root / snapshot_id
    protected = credential_vault.is_available()
    file_entries = []
    dir_entries = []
    try:
        if original.exists():
            if original.is_symlink() or not original.is_dir():
                raise LocalProfileError("Pasta Chromium inválida.")
            for current, dirs, files in os.walk(original, followlinks=False):
                current = Path(current)
                if current.is_symlink():
                    raise LocalProfileError("Link simbólico detectado; backup interrompido.")
                for directory in dirs:
                    if (current / directory).is_symlink():
                        raise LocalProfileError("Backup bloqueado: link simbólico no perfil.")
                    relative = (current / directory).relative_to(original).as_posix()
                    _safe_relative(relative)
                    dir_entries.append(relative)
                for filename in files:
                    source = current / filename
                    if source.is_symlink() or not source.is_file():
                        raise LocalProfileError("Arquivo especial no perfil; backup interrompido.")
                    relative = source.relative_to(original).as_posix()
                    safe = _safe_relative(relative)
                    data = source.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    _private_write(staging / "browser" / (str(safe) + ".dat"), _seal(data))
                    file_entries.append({"path": relative, "sha256": digest})
        # Preserve the existing Windows DPAPI credential reference and its
        # ciphertext, so restoring still works even if the original file goes.
        credential = raw.get("credentialRef")
        if credential and credential.startswith("dpapi:"):
            from nexora_accounts.credential_vault import _folder
            key = str(uuid.UUID(credential[6:]))
            original_secret = _folder(store.path) / (key + ".dpapi")
            if not original_secret.is_file():
                raise LocalProfileError("Credencial protegida da conta ausente; backup incompleto.")
            _private_write(staging / "credential.bin", _seal(original_secret.read_bytes()))
        manifest = {"profile": raw, "directories": dir_entries, "files": file_entries}
        _private_write(staging / "payload.bin", _seal(json.dumps(
            manifest, ensure_ascii=False
        ).encode("utf-8")))
        index = {
            "id": snapshot_id, "accountId": profile["accountId"],
            "profileId": profile_id, "label": profile["label"],
            "origin": profile["origin"], "device": profile.get("deviceLabel") or profile.get("deviceMode"),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "fileCount": len(file_entries), "hasBrowserData": original.is_dir(),
            "encrypted": protected,
        }
        _private_write(staging / "index.json", json.dumps(index).encode("utf-8"))
        os.replace(staging, final)
        return index
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def list_snapshots(*, store=None):
    store = store or LocalProfileStore()
    folder = _root(store)
    if not folder.is_dir():
        return []
    known = {p["accountId"] for p in store.list()}
    output = []
    for entry in folder.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            continue
        try:
            index = json.loads((entry / "index.json").read_text(encoding="utf-8"))
            if entry.name != index["id"]:
                continue
            index["restored"] = index["accountId"] in known
            output.append(index)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(output, key=lambda x: x["createdAt"], reverse=True)


def snapshot_detail(snapshot_id, *, store=None):
    """Authenticated metadata preview; never serve cookie bytes or credentials."""
    store = store or LocalProfileStore()
    try:
        key = str(uuid.UUID(str(snapshot_id)))
        source = _root(store) / key
        if source.is_symlink() or not source.is_dir():
            raise LocalProfileError("Backup não encontrado.")
        index, manifest = _read_manifest(source)
        if index["id"] != key or manifest["profile"]["accountId"] != index["accountId"]:
            raise LocalProfileError("Backup não corresponde à identidade salva.")
        profile = LocalProfileStore.public_view(manifest["profile"], details=True)
        profile["history"] = profile.get("history", [])[-15:]
        profile["browserFileCount"] = len(manifest.get("files", []))
        return profile
    except (OSError, TypeError, KeyError, UnicodeError, ValueError) as exc:
        if isinstance(exc, LocalProfileError):
            raise
        raise LocalProfileError("Metadados do backup inválidos ou inacessíveis.") from exc


def restore_snapshot(snapshot_id, *, store=None):
    """Restore the same UUID/ID/path, original cookies, and saved proxy link."""
    store = store or LocalProfileStore()
    try:
        key = str(uuid.UUID(str(snapshot_id)))
    except (ValueError, TypeError) as exc:
        raise LocalProfileError("ID de backup inválido.") from exc
    snapshot = _root(store) / key
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise LocalProfileError("Backup não encontrado.")
    try:
        index, manifest = _read_manifest(snapshot)
        record = manifest["profile"]
        if index["id"] != key or record["accountId"] != index["accountId"]:
            raise LocalProfileError("Identidade do backup não corresponde.")
        if any(p["id"] == record["id"] or p["accountId"] == record["accountId"]
               for p in store.list()):
            raise LocalProfileError("Conta ou índice já existente; restauração interrompida.")
        from Settings.Criar_Mae.settings.utils import PROFILES_DIR as MAE_DIR
        from Settings.Criar_Filha.settings.utils import PROFILES_DIR as FILHA_DIR
        base = MAE_DIR if record["origin"] == "mae" else FILHA_DIR
        original = stored_profile_directory(base, record)
        if original.exists() or original.is_symlink():
            raise LocalProfileError("A pasta original já existe; não será substituída.")
        parent = original.parent
        parent.mkdir(parents=True, exist_ok=True)
        working = Path(tempfile.mkdtemp(prefix=".restore-incomplete-", dir=parent))
        completed = False
        try:
            for folder in manifest["directories"]:
                (working / _safe_relative(folder)).mkdir(parents=True, exist_ok=True)
            for entry in manifest["files"]:
                relative = _safe_relative(entry["path"])
                stored = snapshot / "browser" / (str(relative) + ".dat")
                data = _unseal(stored.read_bytes(), protected=index["encrypted"])
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise LocalProfileError("Arquivo do backup não passou na verificação de integridade.")
                _private_write(working / relative, data)
            credential = record.get("credentialRef")
            if credential and credential.startswith("dpapi:"):
                from nexora_accounts.credential_vault import _folder
                key_secret = str(uuid.UUID(credential[6:]))
                sealed_credential = snapshot / "credential.bin"
                if not sealed_credential.is_file():
                    raise LocalProfileError("Credencial protegida ausente no backup.")
                _private_write(
                    _folder(store.path) / (key_secret + ".dpapi"),
                    _unseal(sealed_credential.read_bytes(), protected=index["encrypted"])
                )
            os.replace(working, original)
            completed = True
            # Commit metadata only AFTER all browser and credential files exist.
            store.restore_full_record(record)
            proxy_id = record.get("proxyId")
            if proxy_id:
                from nexora_accounts.proxy_store import ProxyStore
                pool = ProxyStore()
                if any(p["id"] == proxy_id for p in pool.list()["proxies"]):
                    pool.bind_profile(record["id"], proxy_id)
            return {"ok": True, "profileId": record["id"], "accountId": record["accountId"]}
        except Exception:
            if not completed:
                shutil.rmtree(working, ignore_errors=True)
            raise
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, LocalProfileError):
            raise
        raise LocalProfileError("Não foi possível validar/restaurar este backup.") from exc
