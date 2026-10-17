"""Pix preparation tests use local fixtures ONLY: no provider, no withdrawals.

A mock transport result is never interpreted as proof of a financial binding.
"""
from types import SimpleNamespace
from uuid import uuid4
import logging

import pytest
from nexora_accounts.pix_operations import (
    BLOCKERS, PixOperationStore, PixPreparationError,
)


def context(tmp_path, *, key_type="PHONE", uid="772104782"):
    account_id, key_id = str(uuid4()), str(uuid4())
    profile = {"id": 17, "accountId": account_id,
               "platformObservedUserId": uid, "platformUserId": None}
    keys = [{"id": key_id, "type": key_type,
             "value": {
                 "PHONE": "11987654321", "EMAIL": "contato@example.org",
                 "CPF": "52998224725", "EVP": "123e4567-e89b-12d3-a456-426614174000",
             }[key_type], "updatedAt": "t1"}]
    profiles = SimpleNamespace(public_detail=lambda id: dict(profile))
    pix_store = SimpleNamespace(list=lambda: {"keys": list(keys)})
    service = PixOperationStore(tmp_path / "history.json",
                                profile_store=profiles, key_store=pix_store)
    return service, profile, keys, {
        "profile_id": profile["id"], "account_id": account_id,
        "key_id": key_id, "key_type": key_type,
        "ownership_acknowledged": True,
    }


@pytest.mark.parametrize("key_type", ["PHONE", "EMAIL", "CPF", "EVP"])
def test_four_pix_types_prepare_without_external_sends_and_survive_restart(
        tmp_path, caplog, key_type):
    service, profile, keys, args = context(tmp_path, key_type=key_type)
    with caplog.at_level(logging.INFO, logger="nexora.pix.operations"):
        result = service.prepare(**args)
    op = result["operation"]
    assert result["reused"] is False
    assert op["status"] == "prepared"
    assert op["providerStatus"] == "not_submitted"
    assert op["externalRequestSent"] is False
    assert op["profileId"] == profile["id"]
    assert op["accountId"] == profile["accountId"]
    assert op["platformIdentityRef"] == "••••782"
    assert op["identityStatus"] == "observed"
    assert op["keyId"] == keys[0]["id"]
    assert op["keyType"] == key_type
    assert op["keyMasked"] != keys[0]["value"]
    assert len(op["missingRequirements"]) == len(BLOCKERS)
    assert all(e["httpMethod"] is None and e["endpoint"] is None
               and e["httpStatus"] is None for e in op["events"])
    assert keys[0]["value"] not in caplog.text
    assert "772104782" not in caplog.text
    assert "123456" not in caplog.text

    assert service.prepare(**args)["reused"] is True
    reopened = PixOperationStore(tmp_path / "history.json",
                                 profile_store=service.profiles,
                                 key_store=service.keys)
    result = reopened.list(profile["id"])
    assert len(result["operations"]) == 1
    assert result["operations"][0]["operationId"] == op["operationId"]
    assert result["providerIntegration"] == "unavailable"
    assert "value" not in str(result["operations"][0])
    assert "772104782" not in str(result)


def test_selection_identity_and_authorization_must_match(tmp_path):
    service, profile, keys, args = context(tmp_path)
    scenarios = [
        {**args, "account_id": str(uuid4())},
        {**args, "key_id": str(uuid4())},
        {**args, "key_type": "CPF"},
        {**args, "ownership_acknowledged": False},
        {**args, "profile_id": 0},
        {**args, "profile_id": True},
    ]
    for bad in scenarios:
        with pytest.raises(PixPreparationError):
            service.prepare(**bad)
    assert service.list()["operations"] == []
    profile["platformObservedUserId"] = None
    with pytest.raises(PixPreparationError, match="ID da plataforma"):
        service.prepare(**args)


def test_key_changes_do_not_silently_reuse_old_preparation(tmp_path):
    service, profile, keys, args = context(tmp_path)
    one = service.prepare(**args)["operation"]["operationId"]
    keys[0]["updatedAt"] = "t2"
    two = service.prepare(**args)["operation"]["operationId"]
    assert one != two
    assert len(service.list()["operations"]) == 2


def test_corrupt_operation_history_is_preserved_not_overwritten(tmp_path):
    service, profile, keys, args = context(tmp_path)
    service.path.write_text("corrupted", encoding="utf-8")
    with pytest.raises(PixPreparationError, match="Histórico"):
        service.prepare(**args)
    assert service.path.read_text(encoding="utf-8") == "corrupted"


def test_no_real_success_can_be_inferred_from_a_fake_http_200(tmp_path):
    service, profile, keys, args = context(tmp_path)
    op = service.prepare(**args)["operation"]
    # No route for external submit or accepting success responses exists.
    fake_reply = {"code": 1, "msg": "ok", "data": {"id": 123}}
    assert fake_reply
    assert op["providerStatus"] == "not_submitted"
    assert op["externalRequestSent"] is False
    assert not hasattr(service, "bind") and not hasattr(service, "submit")


def test_missing_selected_key_type_returns_no_record(tmp_path):
    service, profile, keys, args = context(tmp_path, key_type="EMAIL")
    keys.clear()
    with pytest.raises(PixPreparationError, match="Chave Pix não encontrada"):
        service.prepare(**args)
    assert service.list()["operations"] == []
