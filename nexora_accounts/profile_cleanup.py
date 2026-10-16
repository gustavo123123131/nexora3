"""Delete one saved local account after explicit confirmation.

Never touch another account, the shared legacy directories, or an active
Chromium profile. Fail closed if the session cannot be safely deleted.
"""
from __future__ import annotations

import shutil

from nexora_accounts.browser_sessions import AccountSessionBusy, claim_account
from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore
from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
from Settings.device_modes import stored_profile_directory


def delete_account(profile_id: int, *, remove_files: bool = True) -> dict:
    if type(profile_id) is not int or profile_id < 1:
        raise LocalProfileError("ID da conta inválido.")
    profile = LocalProfileStore().get(profile_id)
    origin = profile["origin"]
    if ACCOUNT_LAUNCHER.is_running(profile_id):
        raise AccountSessionBusy("Feche o navegador desta conta antes de excluir.")
    # The run_all worker also claims this lock for its complete lifetime.
    try:
        with claim_account(origin, profile_id):
            if ACCOUNT_LAUNCHER.is_running(profile_id):
                raise AccountSessionBusy("Feche o navegador desta conta antes de excluir.")
            if origin == "mae":
                from Settings.Criar_Mae.settings.utils import PROFILES_DIR
            elif origin == "filha":
                from Settings.Criar_Filha.settings.utils import PROFILES_DIR
            else:
                raise LocalProfileError("Origem da conta inválida.")
            if remove_files:
                if profile.get("legacySource"):
                    raise LocalProfileError(
                        "Pasta legada sem posse exclusiva comprovada. "
                        "Exclua somente o cartão e preserve os arquivos."
                    )
                try:
                    directory = stored_profile_directory(PROFILES_DIR, profile)
                except ValueError as exc:
                    raise LocalProfileError("Diretório original inválido; exclusão cancelada.") from exc
                if directory.exists():
                    if not directory.is_dir():
                        raise LocalProfileError("Diretório do perfil inválido.")
                    shutil.rmtree(directory)
            # Keep metadata if the folder cannot be deleted. Never remove any
            # legacy profile_1/profile_2 folders with unknown ownership.
            LocalProfileStore().delete(profile_id)
            from nexora_accounts.proxy_store import ProxyStore
            ProxyStore().unbind_deleted_profile(profile_id)
    except AccountSessionBusy:
        raise
    except OSError as exc:
        raise LocalProfileError(
            "Não foi possível apagar os arquivos do navegador. Feche a sessão e tente novamente."
        ) from exc
    return {"ok": True, "profileId": profile_id, "filesRemoved": remove_files}
