"""Offline regression tests for observed (NOT authenticated) 5gbet IDs."""
import pytest
from Settings.platform_identity import (
    FiveGBetIdentityContract, PlatformIdentityObserver, ObservedPlatformIdentity,
    provider_identity_contract,
)
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from tests.test_x_object_id import Page, make, send, PAGE

@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "generated_profiles.json")

def watcher(page, profile, run, contract=None):
    monitor = PlatformIdentityObserver(page, lambda _msg: None,
        {**profile, "runId": run}, expected_origin=PAGE, contract=contract)
    monitor.start()
    return monitor

@pytest.mark.parametrize("api", [
    "https://cdnapiif.ppa5g.net/hall/api/message/list/all",
    "https://cdnapiif.ppa5g.co/hall/api/message/list/all",
])
def test_both_hosts_observe_only_without_verifying(store, api):
    p = make(store, "mae", "run-1")
    page = Page()
    m = watcher(page, p, "run-1")
    try:
        send(page, 772104782, url=api)
        observed = m.observe_candidate()
        assert isinstance(observed, ObservedPlatformIdentity)
        assert observed.user_id == "772104782"
        assert m.identify(wait_seconds=0) is None
        store.save_observed_platform_id(p["id"], p["accountId"], "run-1",
            site_origin=PAGE, observed_identity=observed)
        detail = LocalProfileStore(store.path).public_detail(p["id"])
        assert detail["platformObservedUserId"] == "772104782"
        assert detail["platformUserId"] is None
        assert detail["status"] == "pending"
    finally:
        m.close()

def test_generic_registration_adapter_does_not_disable_5gbet(store):
    class RegistrationOnly: pass
    p = make(store, "mae", "run-1")
    m = watcher(Page(), p, "run-1", RegistrationOnly())
    try:
        assert isinstance(m.contract, FiveGBetIdentityContract)
        assert len(m._header_routes) == 2
    finally:
        m.close()

def test_two_profiles_and_expired_run_isolated(store):
    a, b = make(store, "mae", "run-a"), make(store, "filha", "run-b")
    observers = [watcher(Page(), a, "run-a"), watcher(Page(), b, "run-b")]
    try:
        send(observers[0].page, 772104782)
        send(observers[1].page, 772104783)
        for profile, run, m in [(a, "run-a", observers[0]),
                                 (b, "run-b", observers[1])]:
            store.save_observed_platform_id(profile["id"], profile["accountId"], run,
                site_origin=PAGE, observed_identity=m.observe_candidate())
        assert store.get(a["id"])["platformObservedUserId"] == "772104782"
        assert store.get(b["id"])["platformObservedUserId"] == "772104783"
        store.record_session(a["id"], "starting", "run-new")
        with pytest.raises(LocalProfileError, match="Sessão expirada"):
            store.save_observed_platform_id(a["id"], a["accountId"], "run-a",
                site_origin=PAGE, observed_identity=observers[0].observe_candidate())
    finally:
        for monitor in observers: monitor.close()

def test_late_authentication_without_registration_click(store):
    p = make(store, "filha", "run-late")
    page = Page()
    m = watcher(page, p, "run-late")
    try:
        assert m.observe_candidate() is None
        send(page, 882104789, url="https://cdnapiif.ppa5g.co/hall/api/message/list/all")
        assert m.observe_candidate().user_id == "882104789"
        assert m._clicked_at is None
    finally:
        m.close()

def test_header_conflict_does_not_persist(store):
    p = make(store, "mae", "run-1")
    page = Page()
    m = watcher(page, p, "run-1")
    try:
        send(page, 111111)
        assert m.observe_candidate().user_id == "111111"
        send(page, 222222)
        assert m.observe_candidate() is None
        assert m.observation_conflict is True
        assert store.get(p["id"])["platformObservedUserId"] is None
    finally:
        m.close()
