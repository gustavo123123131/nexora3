"""Regression checks for the existing Abrir Conta storage and safe cleanup.

Never touches the user's account directory or any remote platform.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import pytest

from nexora_accounts.service import AccountsRepository, AccountError
from nexora_accounts.registration_trace import trace_registration


def repository(tmp_path):
    return AccountsRepository(tmp_path / "accounts.json")


def test_repeated_click_does_not_create_duplicate_records(tmp_path):
    repo = repository(tmp_path)
    base = tmp_path / "profiles"
    plans = repo.create_launch_profiles("mae", "desktop",
                                        ["https://example.test/register"], 2, base)
    assert len(plans) == 2
    with pytest.raises(AccountError, match="nenhum perfil duplicado"):
        repo.create_launch_profiles("mae", "desktop",
                                    ["https://example.test/register"], 2, base)
    persisted = AccountsRepository(repo.path)
    assert len(persisted.accounts(profiles_only=True)) == 2
    assert len({a["id"] for a in persisted.accounts(profiles_only=True)}) == 2
    assert len({a["profileIndex"] for a in persisted.accounts(profiles_only=True)}) == 2


def test_concurrent_double_click_is_atomic(tmp_path):
    repo = repository(tmp_path)
    base = tmp_path / "profiles"

    def click(_):
        try:
            return repo.create_launch_profiles(
                "filha", "android", ["http://127.0.0.1:8000/register"], 2, base)
        except AccountError:
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        both = list(pool.map(click, range(2)))
    assert sorted(len(x) for x in both) == [0, 2]
    assert len(repo.accounts(profiles_only=True)) == 2


def test_another_intent_can_create_separate_profiles(tmp_path):
    repo = repository(tmp_path)
    base = tmp_path / "profiles"
    one = repo.create_launch_profiles("mae", "desktop",
                                      ["http://localhost:8000/register"], 2, base)
    two = repo.create_launch_profiles("mae", "desktop",
                                      ["http://localhost:8000/register"], 1, base)
    assert len(one) == 2 and len(two) == 1
    assert len({x["profile_record_id"] for x in one + two}) == 3


def test_cleanup_preview_backup_and_keep_uncertain_or_legitimate_records(tmp_path):
    repo = repository(tmp_path)
    root = tmp_path / "profiles"
    plans = repo.create_launch_profiles(
        "mae", "desktop", ["http://localhost:8088/register"], 3, root)
    first, second, third = [x["profile_record_id"] for x in plans]
    # Status after submit must never be removed without separate investigation.
    repo.set_stage(second, "pending", "awaiting-provider")
    repo.set_stage(third, "completed", "provider-confirmed")
    before = repo.path.read_bytes()
    preview = repo.cleanup_test_candidates()
    assert preview["deleted"] == 0 and repo.path.read_bytes() == before
    assert [x["id"] for x in preview["candidates"]] == [first]
    result = repo.cleanup_test_candidates(apply=True)
    assert result["deleted"] == 1
    assert Path(result["backup"]).read_bytes() == before
    assert len(repo.accounts(profiles_only=True)) == 2
    assert {a["id"] for a in repo.accounts(profiles_only=True)} == {second, third}
    # No browser profile folder is touched, even when the record is removed.
    from Settings.device_modes import profile_directory
    assert all(profile_directory(root, "desktop", p["idx"]).exists() for p in plans)


def test_legacy_records_remain_out_of_profiles_only_view(tmp_path):
    repo = repository(tmp_path)
    repo.add_site({"id": "legacy", "displayName": "Antigo",
                   "baseUrl": "https://example.org",
                   "registrationPath": "/register", "loginPath": "/login"})
    legacy = repo.add_account({"siteId": "legacy", "displayName": "Conta legítima existente",
                               "profileScope": "mae", "profileMode": "desktop",
                               "profileIndex": 7})
    assert any(a["id"] == legacy["id"] for a in repo.accounts())
    assert not any(a["id"] == legacy["id"] for a in repo.accounts(profiles_only=True))
    assert repo.cleanup_test_candidates(apply=True)["deleted"] == 0
    assert repo.account(legacy["id"])["displayName"] == "Conta legítima existente"


def test_trace_persists_timestamps_profile_id_and_elapsed_but_no_credentials(tmp_path, capsys):
    repo = repository(tmp_path)
    plan = repo.create_launch_profiles("filha", "desktop",
                                       ["http://localhost:8181/register"], 1,
                                       tmp_path / "profiles")[0]
    key = plan["profile_record_id"]
    trace_registration(key, plan["idx"], "browser-starting", ui_log=None, repository=repo)
    trace_registration(key, plan["idx"], "browser-ready", ui_log=None, repository=repo)
    captured = capsys.readouterr().out
    assert key[:8] in captured
    assert "[browser-ready]" in captured
    assert " ms" in captured
    assert "password" not in captured.lower()
    log = repo.account(key)["registrationLog"]
    assert log[-1]["stage"] == "browser-ready"
    assert isinstance(log[-1]["durationMs"], int)
    assert "profileData" not in json.dumps(log)


def test_list_and_state_persist_after_reload(tmp_path):
    repo = repository(tmp_path)
    plans = repo.create_launch_profiles("mae", "iphone", ["http://localhost:9090"], 2,
                                        tmp_path / "profiles")
    key = plans[0]["profile_record_id"]
    repo.set_stage(key, "local", "site-loading", duration_ms=22)
    reloaded = AccountsRepository(repo.path)
    assert reloaded.account(key)["stage"] == "site-loading"
    assert len(reloaded.accounts(profiles_only=True)) == 2
    assert reloaded.account(key)["registrationLog"][-1]["durationMs"] == 22
