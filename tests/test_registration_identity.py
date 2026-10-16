"""Offline-only checks of session-bound registration and platform identity storage."""
import json

import pytest

from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "generated_profiles.json")


def make_account(store, name, number):
    p = store.create(
        name, device_mode="android",
        start_url="https://platform.example.test/register"
    )
    store.record_session(p["id"], "starting", "test-run-" + str(number))
    return p


def verified(store, p, run_id, platform_user_id=None):
    return store.update_registration_result(
        p["id"], p["accountId"], run_id, "created",
        "Confirmação sintética de teste",
        confirmation_source="synthetic-contract-only",
        site_origin="https://platform.example.test",
        platform_user_id=platform_user_id,
        platform_id_source=("synthetic-test-remote-id" if platform_user_id else None),
    )


def test_unverified_status_does_not_create_or_invent_platform_id(store):
    p = make_account(store, "mae", 1)
    with pytest.raises(LocalProfileError):
        store.update_status(p["id"], "created", "HTTP 200")
    with pytest.raises(LocalProfileError):
        store.update_registration_result(
            p["id"], p["accountId"], "test-run-1", "created", "HTTP 200",
            site_origin="https://platform.example.test",
        )
    item = store.update_registration_result(
        p["id"], p["accountId"], "test-run-1", "unconfirmed",
        "Sem evidência do contrato",
    )
    assert item["platformUserId"] is None
    assert item["status"] == "unconfirmed"
    assert LocalProfileStore(store.path).get(p["id"])["platformUserId"] is None


def test_confirmed_without_id_and_later_id_preserves_leading_zeroes(store):
    p = make_account(store, "filha", 2)
    first = verified(store, p, "test-run-2")
    assert first["status"] == "created"
    assert first["platformUserId"] is None
    assert first["registrationConfirmedAt"]
    assert first["platformIdVerifiedAt"] is None
    second = verified(store, p, "test-run-2", "000012345")
    assert second["platformUserId"] == "000012345"
    assert second["platformIdSource"] == "synthetic-test-remote-id"
    assert second["platformIdVerifiedAt"]
    assert second["registrationConfirmedAt"] == first["registrationConfirmedAt"]
    again = LocalProfileStore(store.path).public_detail(p["id"])
    assert again["platformUserId"] == "000012345"
    assert again["accountId"] == p["accountId"]
    with pytest.raises(LocalProfileError, match="Conflito"):
        verified(store, p, "test-run-2", "999999999")
    assert store.get(p["id"])["platformUserId"] == "000012345"


def test_late_run_wrong_uuid_and_cross_origin_cannot_write(store):
    p = make_account(store, "mae", 3)
    q = make_account(store, "filha", 4)
    store.record_session(p["id"], "starting", "new-run-3")
    with pytest.raises(LocalProfileError, match="execução diferente"):
        verified(store, p, "test-run-3", "11")
    with pytest.raises(LocalProfileError, match="conta original"):
        store.update_registration_result(
            p["id"], q["accountId"], "new-run-3", "created",
            confirmation_source="synthetic-contract",
            site_origin="https://platform.example.test",
        )
    with pytest.raises(LocalProfileError, match="Origem"):
        store.update_registration_result(
            p["id"], p["accountId"], "new-run-3", "created",
            confirmation_source="synthetic-contract",
            site_origin="https://evil.test",
            platform_user_id="5", platform_id_source="synthetic-id",
        )
    verified(store, q, "test-run-4", "000004")
    with pytest.raises(LocalProfileError, match="outra conta"):
        verified(store, p, "new-run-3", "000004")
    assert store.get(p["id"])["platformUserId"] is None
    assert store.get(q["id"])["platformUserId"] == "000004"


def test_verification_required_is_not_rejection(store):
    p = make_account(store, "mae", 5)
    x = store.update_registration_result(
        p["id"], p["accountId"], "test-run-5", "verification_required",
        "Etapa adicional documentada",
    )
    assert x["status"] == "verification_required"
    assert x["registrationConfirmedAt"] is None
    assert x["platformUserId"] is None
    x = store.update_registration_result(
        p["id"], p["accountId"], "test-run-5", "unconfirmed",
        "Prazo terminado sem conclusão",
    )
    assert x["status"] == "unconfirmed"


def test_legacy_created_is_unchanged_but_marked_for_review(store):
    p = store.create("mae", device_mode="iphone")
    content = json.loads(store.path.read_text(encoding="utf-8"))
    entry = next(x for x in content["profiles"] if x["id"] == p["id"])
    entry["status"] = "created"
    for field in ("registrationReviewRequired", "registrationConfirmationSource",
                  "registrationConfirmedAt", "platformUserId", "platformIdSource",
                  "platformIdVerifiedAt"):
        entry.pop(field, None)
    store.path.write_text(json.dumps(content), encoding="utf-8")
    restored = LocalProfileStore(store.path)
    x = restored.get(p["id"])
    assert x["status"] == "created"
    assert x["registrationReviewRequired"] is True
    assert x["platformUserId"] is None
    assert x["accountId"] == p["accountId"]
    assert x["deviceProfileId"] == p["deviceProfileId"]
    assert LocalProfileStore(store.path).get(p["id"])["registrationReviewRequired"] is True


def test_invalid_remote_identity_never_persisted(store):
    p = make_account(store, "mae", 6)
    for invalid in ("", "a b", "token\nleak", "x"*129, 12345):
        with pytest.raises(LocalProfileError):
            verified(store, p, "test-run-6", invalid)
    assert store.get(p["id"])["status"] == "pending"


def test_delayed_verified_id_after_confirmation_and_reopen(store):
    p = make_account(store, "mae", 7)
    verified(store, p, "test-run-7")
    assert store.get(p["id"])["platformUserId"] is None
    persisted = store.save_verified_platform_id(
        p["id"], p["accountId"], "test-run-7",
        site_origin="https://platform.example.test",
        platform_user_id="0000079", platform_id_source="synthetic-authenticated",
    )
    assert persisted["platformUserId"] == "0000079"
    assert persisted["platformIdSource"] == "synthetic-authenticated"
    assert persisted["platformIdVerifiedAt"]
    reopened = LocalProfileStore(store.path).public_detail(p["id"])
    assert reopened["platformUserId"] == "0000079"
    assert reopened["accountId"] == p["accountId"]
    assert reopened["registrationConfirmedAt"]


def test_late_id_from_previous_run_cannot_change_account(store):
    p = make_account(store, "filha", 8)
    verified(store, p, "test-run-8")
    store.record_session(p["id"], "starting", "new-run-8")
    with pytest.raises(LocalProfileError, match="expirada"):
        store.save_verified_platform_id(
            p["id"], p["accountId"], "test-run-8",
            site_origin="https://platform.example.test",
            platform_user_id="0000080", platform_id_source="synthetic-authenticated",
        )
    assert store.get(p["id"])["platformUserId"] is None


def test_id_requires_correct_account_origin_and_verified_status(store):
    p = make_account(store, "mae", 9)
    with pytest.raises(LocalProfileError, match="confirmado"):
        store.save_verified_platform_id(
            p["id"], p["accountId"], "test-run-9",
            site_origin="https://platform.example.test",
            platform_user_id="0000090", platform_id_source="synthetic-authenticated",
        )
    verified(store, p, "test-run-9")
    wrong_uuid = "00000000-0000-4000-8000-000000000009"
    with pytest.raises(LocalProfileError, match="local"):
        store.save_verified_platform_id(
            p["id"], wrong_uuid, "test-run-9",
            site_origin="https://platform.example.test",
            platform_user_id="0000090", platform_id_source="synthetic-authenticated",
        )
    with pytest.raises(LocalProfileError, match="Origem"):
        store.save_verified_platform_id(
            p["id"], p["accountId"], "test-run-9",
            site_origin="https://different.test",
            platform_user_id="0000090", platform_id_source="synthetic-authenticated",
        )
    assert store.get(p["id"])["platformUserId"] is None


def test_unique_id_per_platform_and_no_overwrite(store):
    p, q = make_account(store, "mae", 10), make_account(store, "filha", 11)
    verified(store, p, "test-run-10")
    verified(store, q, "test-run-11")
    args = dict(site_origin="https://platform.example.test",
                platform_user_id="0000010",
                platform_id_source="synthetic-authenticated")
    store.save_verified_platform_id(p["id"], p["accountId"], "test-run-10", **args)
    with pytest.raises(LocalProfileError, match="outra conta"):
        store.save_verified_platform_id(q["id"], q["accountId"], "test-run-11", **args)
    with pytest.raises(LocalProfileError, match="Conflito"):
        store.save_verified_platform_id(
            p["id"], p["accountId"], "test-run-10",
            site_origin="https://platform.example.test",
            platform_user_id="9990010", platform_id_source="synthetic-authenticated",
        )
    assert store.get(p["id"])["platformUserId"] == "0000010"
    assert store.get(q["id"])["platformUserId"] is None


def test_legacy_created_requires_review_before_remote_id(store):
    p = make_account(store, "mae", 12)
    store.record_session(p["id"], "starting", "test-run-12")
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["profiles"][0]["status"] = "created"
    data["profiles"][0]["registrationReviewRequired"] = True
    data["profiles"][0]["registrationConfirmedAt"] = None
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LocalProfileError, match="confirmado"):
        LocalProfileStore(store.path).save_verified_platform_id(
            p["id"], p["accountId"], "test-run-12",
            site_origin="https://platform.example.test",
            platform_user_id="0000120", platform_id_source="synthetic-authenticated",
        )
