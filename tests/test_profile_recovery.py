"""Controlled recovery of saved account metadata and orphan detection."""
from pathlib import Path

import pytest

from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError


def test_orphan_missing_and_manual_restore_preserve_uuid_and_profile_path(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils
    root = tmp_path / "__profiles"
    monkeypatch.setattr(utils, "PROFILES_DIR", root)
    store = LocalProfileStore(tmp_path / "Nexora" / "generated_profiles.json")
    a = store.create("mae", device_mode="android")
    b = store.create("mae", device_mode="android")
    existing = Path(b["profilePath"])
    existing.mkdir(parents=True)
    (existing / "cookie.txt").write_text("keep")
    unused = root / "accounts" / "account_999"
    unused.mkdir(parents=True)

    report = store.integrity_report()
    assert any(item["accountId"] == a["accountId"] for item in report["missing"])
    assert str(unused.resolve()) in report["orphanFolders"]

    store.delete(b["id"])
    assert existing.exists()  # Delete of metadata leaves Chromium folder untouched.
    candidates = store.recovery_candidates()
    assert any(item["accountId"] == b["accountId"] for item in candidates)

    restored = store.restore_account_from_backup(b["accountId"])
    assert restored["accountId"] == b["accountId"]
    assert restored["deviceProfileId"] == b["deviceProfileId"]
    assert restored["profilePath"] == b["profilePath"]
    assert (existing / "cookie.txt").read_text() == "keep"
    with pytest.raises(LocalProfileError, match="já está"):
        store.restore_account_from_backup(b["accountId"])
