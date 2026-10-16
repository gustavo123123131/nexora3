"""Tests for the operator-requested LOCAL 'Criada after click' display policy.

No HTTP responses, sites or Chromium accounts are contacted. A clicked
registration is intentionally NOT a provider-verified registration.
"""
import pytest

from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError


ORIGIN = "https://platform.example.test"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "profiles.json")


def make(store, origin, run):
    profile = store.create(
        origin, device_mode="android", start_url=ORIGIN + "/register"
    )
    store.record_session(profile["id"], "starting", run)
    return profile


def clicked(store, p, run):
    return store.mark_registration_clicked(
        p["id"], p["accountId"], run, site_origin=ORIGIN
    )


def test_click_sets_created_immediately_and_persists_without_provider_evidence(store):
    p = make(store, "mae", "run-110")
    record = clicked(store, p, "run-110")
    assert record["status"] == "created"
    assert record["registrationClickedAt"]
    assert record["registrationConfirmedAt"] is None
    assert record["registrationConfirmationSource"] is None
    assert record["registrationReviewRequired"] is True
    assert record["platformUserId"] is None
    assert "sem confirmação" in record["statusMessage"]
    # An on-disk reopened instance shows 'Criada' without re-running a browser.
    restored = LocalProfileStore(store.path).public_detail(p["id"])
    assert restored["status"] == "created"
    assert restored["registrationClickedAt"] == record["registrationClickedAt"]
    assert restored["accountId"] == p["accountId"]


def test_click_only_changes_matching_account_and_session(store):
    p = make(store, "mae", "run-110")
    q = make(store, "filha", "run-111")
    clicked(store, p, "run-110")
    assert store.get(p["id"])["status"] == "created"
    assert store.get(q["id"])["status"] == "pending"
    with pytest.raises(LocalProfileError, match="outra conta"):
        store.mark_registration_clicked(
            p["id"], q["accountId"], "run-110", site_origin=ORIGIN
        )
    with pytest.raises(LocalProfileError, match="antiga"):
        store.mark_registration_clicked(
            q["id"], q["accountId"], "old-run", site_origin=ORIGIN
        )
    with pytest.raises(LocalProfileError, match="Origem"):
        store.mark_registration_clicked(
            q["id"], q["accountId"], "run-111", site_origin="https://other.test"
        )
    assert store.get(q["id"])["status"] == "pending"


def test_click_status_survives_browser_close_and_repeated_click(store):
    p = make(store, "filha", "run-110")
    first = clicked(store, p, "run-110")
    second = clicked(store, p, "run-110")
    assert second["registrationClickedAt"] == first["registrationClickedAt"]
    store.record_session(p["id"], "closed", "run-110")
    assert LocalProfileStore(store.path).get(p["id"])["status"] == "created"


def test_actual_provider_verified_account_not_downgraded(store):
    p = make(store, "mae", "run-110")
    verified = store.update_registration_result(
        p["id"], p["accountId"], "run-110",
        "created", "Proved by synthetic reviewed contract",
        confirmation_source="synthetic-reviewed-response",
        site_origin=ORIGIN,
    )
    recorded = clicked(store, p, "run-110")
    assert recorded["registrationConfirmedAt"] == verified["registrationConfirmedAt"]
    assert recorded["registrationConfirmationSource"] == (
        verified["registrationConfirmationSource"]
    )
    assert recorded["registrationReviewRequired"] is False


def test_failed_click_does_not_use_created_shortcut(store):
    p = make(store, "mae", "run-110")
    # The GUI calls mark_registration_clicked only on fill_registration_form==True.
    store.update_registration_result(
        p["id"], p["accountId"], "run-110",
        "error", "Formulário ou clique falhou.",
    )
    assert LocalProfileStore(store.path).get(p["id"])["status"] == "error"


def test_both_tabs_write_click_status_only_on_success():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1]
    for area in ("Criar_Mae", "Criar_Filha"):
        source = (base / "Settings" / area / "gui_tab.py").read_text(encoding="utf-8")
        assert "clicked = fill_registration_form(page, profile, log, stop_event)" in source
        assert "if clicked:" in source
        assert "store.mark_registration_clicked(" in source
        assert "observer.finish(stop_event)" not in source
