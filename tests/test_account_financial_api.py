"""Account-scoped financial identity and Pix readiness regressions.

No provider calls, no external withdrawal or Pix binding is performed.
"""
import json
from uuid import uuid4

import pytest

from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from nexora_accounts.pix_operations import PixOperationStore, PixPreparationError
from nexora_accounts.pix_key_store import PixKeyStore


def _verified_store(tmp_path):
    path = tmp_path / "profile.json"
    store = LocalProfileStore(path)
    one = store.create("mae")
    two = store.create("filha")
    data = json.loads(path.read_text(encoding="utf-8"))
    for item, uid in zip(data["profiles"], ("70011122", "70033344")):
        item["platformUserId"] = uid
        item["platformIdSource"] = "test-verified"
    store._write(data)
    return store, one, two


def test_new_password_default_and_legacy_not_retroactively_changed(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXORA_NEW_ACCOUNT_PASSWORD", raising=False)
    store, one, two = _verified_store(tmp_path)
    first_password = store.get(one["id"])["password"]
    second_password = store.get(two["id"])["password"]
    assert len(first_password) == 9 and len(second_password) == 9
    assert first_password != "387520"
    assert second_password != "387520"
    assert first_password != second_password
    assert store.get(one["id"])["confirmPassword"] == first_password
    assert store.get(two["id"])["confirmPassword"] == second_password
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["profiles"][0]["password"] = "legacy-secret"
    data["profiles"][0]["confirmPassword"] = "legacy-secret"
    store._write(data)
    reopened = LocalProfileStore(store.path)
    assert reopened.get(one["id"])["password"] == "legacy-secret"
    assert reopened.get(two["id"])["password"] == second_password
    assert "password" not in reopened.public_detail(one["id"])


def test_financial_identity_is_scoped_to_one_account_and_survives_restart(tmp_path):
    store, one, two = _verified_store(tmp_path)
    with pytest.raises(LocalProfileError):
        store.set_financial_identity(one["id"], two["accountId"],
                                     "Eduardo Moreira", "52998224725")
    with pytest.raises(LocalProfileError):
        store.set_financial_identity(one["id"], one["accountId"],
                                     "Eduardo Moreira", "00000000000")
    detail = store.set_financial_identity(one["id"], one["accountId"],
                                           "Eduardo Moreira", "529.982.247-25")
    assert detail["financialRealName"] == "Eduardo Moreira"
    assert detail["financialCpf"] == "52998224725"
    reopened = LocalProfileStore(store.path)
    assert reopened.public_detail(one["id"])["financialRealName"] == "Eduardo Moreira"
    assert reopened.public_detail(two["id"])["financialRealName"] is None
    assert reopened.public_list()[0].get("financialCpf") is None


def test_withdraw_pin_is_distinct_from_login_password_and_hidden(tmp_path, monkeypatch):
    from nexora_accounts import credential_vault
    store, one, _ = _verified_store(tmp_path)
    saved = {}
    monkeypatch.setattr(credential_vault, "is_available", lambda: True)
    monkeypatch.setattr(credential_vault, "write_withdraw_pin",
                        lambda path, uid, pin: saved.setdefault(uid, pin) and "withdraw-dpapi:" + uid)
    monkeypatch.setattr(credential_vault, "read_withdraw_pin",
                        lambda path, ref: saved[ref.split(":", 1)[1]])
    before = store.get(one["id"])["password"]
    result = store.set_withdraw_pin(one["id"], one["accountId"], "387520")
    assert result["hasWithdrawPin"] is True
    assert "withdrawPinRef" not in result
    assert store.get(one["id"])["password"] == before
    assert store.read_withdraw_pin(one["id"]) == "387520"
    assert "withdraw-dpapi:" in store.path.read_text(encoding="utf-8")
    assert "withdrawPinRef" not in str(result)
    assert "387520" not in str(result)


def test_pix_readiness_checks_verified_owner_account_pin_and_saved_type(tmp_path):
    store, one, two = _verified_store(tmp_path)
    pix = PixKeyStore(tmp_path / "keys.json")
    key = pix.add("contato@example.org", "EMAIL")["key"]
    service = PixOperationStore(tmp_path / "operations.json",
                                profile_store=store, key_store=pix)
    args = dict(profile_id=one["id"], account_id=one["accountId"],
                key_id=key["id"], key_type="EMAIL")
    initial = service.readiness(**args)
    assert initial["readyForProvider"] is False
    assert initial["externalExecutionEnabled"] is False
    assert any("Nome real" in item for item in initial["missingRequirements"])
    with pytest.raises(PixPreparationError):
        service.readiness(**{**args, "account_id": two["accountId"]})
    with pytest.raises(PixPreparationError):
        service.readiness(**{**args, "key_type": "PHONE"})
    store.set_financial_identity(one["id"], one["accountId"], "Eduardo Moreira", "52998224725")
    ready = service.readiness(**args)
    assert ready["readyForProvider"] is False
    assert any("PIN" in item for item in ready["missingRequirements"])
