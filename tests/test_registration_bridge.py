"""Tests of registration bridge: no remote sites, no real registrations."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import servidor
from nexora_accounts.service import (
    AccountsRepository, AccountsService, AccountError,
    _site_config, validate_values,
)


def site_config(authorized=False):
    return {
        "id": "site_teste", "displayName": "Site de teste",
        "baseUrl": "https://example.com", "registrationPath": "/register",
        "loginPath": "/login", "authorized": authorized,
        "fields": {
            "username": {"selector": "#username", "required": True},
            "password": {"selector": "#password", "required": True},
        } if authorized else {},
        "submitSelector": "#submit" if authorized else "",
        "successSelector": "#success" if authorized else "",
    }


def test_site_guard_validation_and_store(tmp_path):
    path = tmp_path / "contas.json"
    repo = AccountsRepository(path)
    with pytest.raises(AccountError):
        repo.add_site({**site_config(), "baseUrl": "file:///etc/passwd"})
    with pytest.raises(AccountError):
        repo.add_site({**site_config(), "authorized": True})
    with pytest.raises(AccountError):
        repo.add_site({**site_config(), "registrationPath": "//evil.example/register"})
    site = repo.add_site(site_config(authorized=True))
    assert site["registrationUrl"] == "https://example.com/register"
    assert site["loginUrl"] == "https://example.com/login"
    assert repo.sites()[0]["authorized"] is True

    mother = repo.add_account({
        "siteId": "site_teste", "displayName": "Mãe local",
        "profileScope": "mae", "profileMode": "desktop",
        "profileIndex": 1, "password": "NAO_SALVAR_SEGREDO",
    })
    child = repo.add_account({
        "siteId": "site_teste", "displayName": "Filha local",
        "profileScope": "filha", "parentId": mother["id"],
        "profileMode": "android", "profileIndex": 2,
    })
    assert child["parentId"] == mother["id"]
    assert mother["status"] == "local"
    assert "NAO_SALVAR_SEGREDO" not in path.read_bytes().decode("utf-8", errors="ignore")
    assert len(repo.accounts()) == 2
    assert repo.accounts(query="filha")[0]["id"] == child["id"]
    with pytest.raises(AccountError):
        repo.add_account({
            "siteId": "site_teste", "displayName": "Incorreta",
            "profileScope": "mae", "parentId": mother["id"],
        })
    with pytest.raises(AccountError):
        repo.add_account({
            "siteId": "site_teste", "displayName": "Inválida",
            "profileMode": "app", "profileIndex": -1,
        })
    # Status "completed" is only writable by internal verified-outcome logic;
    # the HTTP API has no arbitrary status-update endpoint.
    repo.set_stage(child["id"], "completed", "provider-confirmed")
    assert repo.account(child["id"])["status"] == "completed"
    repo.set_stage(child["id"], "pending", "review")
    assert repo.account(child["id"])["status"] == "pending"
    repo.delete(mother["id"])
    assert repo.account(child["id"])["parentId"] is None
    # Persistence after reopening (no credentials).
    assert len(AccountsRepository(path).accounts()) == 1
    assert len(json.loads(path.read_text(encoding="utf-8"))["audit"]) >= 5


def test_form_checks_cpf_and_confirmation():
    definitions = {
        "username": {"required": True}, "password": {"required": True},
        "confirmPassword": {"required": True}, "cpf": {"required": False},
    }
    values = {"username": "user_21", "password": "Senha-Forte-123",
              "confirmPassword": "errada", "cpf": "11111111111"}
    _, errors = validate_values(values, definitions)
    assert "confirmPassword" in errors and "cpf" in errors
    assert "Senha-Forte-123" not in repr(errors)
    _, errors = validate_values(
        {"username": "user_21", "password": "Senha-Forte-123",
         "confirmPassword": "Senha-Forte-123"}, definitions)
    assert errors == {}


def test_routes_have_csrf_and_do_not_touch_cloud(tmp_path, monkeypatch):
    # Replace the persistent default storage with a test-only instance.
    monkeypatch.setattr(servidor, "_accounts_service",
                        AccountsService(tmp_path / "api.json", start_browser=False))
    with TestClient(servidor.app) as client:
        assert client.get("/static/accounts_view.js").status_code == 200
        assert client.get("/static/accounts_view.css").status_code == 200
        assert client.get("/api/registration/sites").json() == {"sites": []}
        assert client.post("/api/registration/sites", json=site_config()).status_code == 403
        csrf = client.get("/api/state").json()["csrf_token"]
        headers = {"X-LixoCPA-CSRF": csrf}
        response = client.post("/api/registration/sites", headers=headers,
                               json=site_config())
        assert response.status_code == 200, response.text
        assert response.json()["site"]["authorized"] is False
        response = client.post("/api/registration/accounts", headers=headers, json={
            "siteId": "site_teste", "displayName": "Conta 1",
            "profileScope": "mae", "profileMode": "desktop", "profileIndex": 1,
        })
        assert response.status_code == 200, response.text
        account = response.json()["account"]
        assert account["status"] == "local"
        assert len(client.get("/api/registration/accounts").json()["accounts"]) == 1
        response = client.post("/api/registration/prepare",
                               headers=headers, json={"accountId": account["id"]})
        # No authorized browser configured, so this is rejected before any Playwright.
        # With start_browser=False the browser is intentionally absent in tests;
        # invalid profile controls remain rejected by repository.
        assert response.status_code >= 400


def test_cloud_posts_blocked_on_registration(monkeypatch):
    monkeypatch.setenv("CODESPACES", "true")
    monkeypatch.setenv("CODESPACE_NAME", "branch-test-987")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    with TestClient(servidor.app, base_url="https://branch-test-987-8765.app.github.dev") as client:
        assert client.post("/api/registration/accounts", json={}).status_code == 403
        assert client.post("/api/registration/sites", json={}).status_code == 403


def test_json_batch_atomic_and_legacy_import(tmp_path):
    db = tmp_path / "accounts.json"
    repo = AccountsRepository(db)
    repo.add_site(site_config(authorized=True))
    first = repo.add_account({
        "siteId": "site_teste", "displayName": "Mãe original",
        "profileScope": "mae", "profileMode": "desktop", "profileIndex": 1,
    })
    batch = repo.create_batch({
        "siteId": "site_teste", "displayName": "Lote local",
        "profileScope": "mae", "profileMode": "desktop", "quantity": 3,
    })
    assert batch["quantity"] == 3
    state = json.loads(db.read_text(encoding="utf-8"))
    assert len(state["accounts"]) == 4
    assert len(state["batches"]) == 1
    assert len(state["jobs"]) == 3
    assert sorted(a["profileIndex"] for a in state["accounts"]) == [1, 2, 3, 4]
    repo.set_stage(batch["account"]["id"], "pending", "review")
    state = json.loads(db.read_text(encoding="utf-8"))
    assert state["batches"][0]["status"] == "running"
    assert AccountsRepository(db).account(first["id"])["displayName"] == "Mãe original"
    before = db.read_bytes()
    with pytest.raises(AccountError):
        repo.create_batch({"siteId": "site_teste", "displayName": "Inválido",
                           "profileScope": "mae", "profileMode": "desktop",
                           "quantity": 101})
    assert db.read_bytes() == before

    # A damaged JSON file is never overwritten or reset.
    db.write_text("{unparseable", encoding="utf-8")
    with pytest.raises(AccountError):
        AccountsRepository(db)
    assert db.read_text(encoding="utf-8") == "{unparseable"

    old = tmp_path / "accounts.sqlite"
    with sqlite3.connect(old) as sql:
        sql.executescript("""
        CREATE TABLE sites(id TEXT, config TEXT, created_at TEXT);
        CREATE TABLE accounts(
          id TEXT,site_id TEXT,display_name TEXT,login_identifier TEXT,parent_id TEXT,
          profile_scope TEXT,profile_mode TEXT,profile_index INTEGER,status TEXT,
          stage TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE account_audit(action TEXT,entity TEXT,occurred_at TEXT);
        """)
        sql.execute("INSERT INTO sites VALUES(?,?,?)",
                    ("site_teste", json.dumps(site_config()), "2026-10-14"))
        sql.execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (first["id"], "site_teste", "Conta antiga", "", None,
                     "mae", "desktop", 1, "local", "saved",
                     "2026-10-14", "2026-10-14"))
    db.unlink()
    migrated = AccountsRepository(db)
    assert migrated.account(first["id"])["displayName"] == "Conta antiga"
    assert old.exists()


def test_json_batch_api_with_csrf(tmp_path, monkeypatch):
    monkeypatch.setattr(servidor, "_accounts_service",
                        AccountsService(tmp_path / "batch.json", start_browser=False))
    with TestClient(servidor.app) as client:
        csrf = client.get("/api/state").json()["csrf_token"]
        headers = {"X-LixoCPA-CSRF": csrf}
        assert client.post("/api/registration/sites", headers=headers,
                           json=site_config()).status_code == 200
        result = client.post("/api/registration/accounts", headers=headers, json={
            "siteId": "site_teste", "displayName": "Lote",
            "profileScope": "filha", "profileMode": "iphone",
            "profileIndex": 1, "quantity": 3,
        })
        assert result.status_code == 200, result.text
        assert result.json()["quantity"] == 3
        assert len(client.get("/api/registration/accounts").json()["accounts"]) == 3
        assert client.post("/api/registration/cloud/sync", headers=headers, json={}).status_code == 404


def test_new_profiles_have_unique_test_data_valid_cpf_and_persistent_folders(tmp_path):
    """No remote API calls; each profile has its own JSON identity and Chromium directory."""
    from Settings.device_modes import profile_directory
    from nexora_accounts.profile_data import valid_cpf_digits
    folder = tmp_path / "profiles"
    repo = AccountsRepository(tmp_path / "accounts.json")
    first = repo.create_launch_profiles("mae", "desktop",
                                        ["https://example.test", "https://site.example.test"],
                                        3, folder)
    second = repo.create_launch_profiles("mae", "desktop", [], 2, folder)
    child = repo.create_launch_profiles("filha", "android", [], 2,
                                        tmp_path / "daughter_profiles")
    assert len(first) == 6 and len(second) == 2 and len(child) == 2
    assert [p["idx"] for p in first] == list(range(1, 7))
    assert [p["idx"] for p in second] == [7, 8]
    assert [p["url"] for p in first] == ["https://example.test"] * 3 + ["https://site.example.test"] * 3
    assert all(profile_directory(folder, "desktop", p["idx"]).is_dir() for p in first + second)
    accounts = repo.accounts()
    assert len(accounts) == 10
    assert len({a["id"] for a in accounts}) == 10
    assert len({a["profileData"]["username"] for a in accounts}) == 10
    assert len({a["profileData"]["password"] for a in accounts}) == 10
    assert len({a["profileData"]["email"] for a in accounts}) == 10
    for acc in accounts:
        d = acc["profileData"]
        cpf = "".join(c for c in d["cpf"] if c.isdigit())
        assert valid_cpf_digits(cpf[:9]) == cpf
        assert d["isTestData"] is True and d["cpfVerified"] is False
        assert d["email"].endswith("@example.test")
        assert d["fullName"].startswith("Pessoa Teste ")
        pwd = d["password"]
        assert len(pwd) == 9
        assert not d["username"].startswith("teste")
        assert any(c.islower() for c in pwd)
        assert any(c.isupper() for c in pwd)
        assert any(c.isdigit() for c in pwd)
        assert any(c in "#!@?" for c in pwd)
    assert len(AccountsRepository(tmp_path / "accounts.json").accounts()) == 10
    assert any(a["profileScope"] == "filha" for a in accounts)


def test_profile_creation_does_not_claim_external_registration(tmp_path):
    repo = AccountsRepository(tmp_path / "accounts.json")
    profiles = repo.create_launch_profiles("mae", "desktop", [], 1, tmp_path / "profiles")
    assert profiles[0]["url"] == ""
    account = repo.accounts()[0]
    assert account["status"] == "local"
    assert account["stage"] == "profile-created"
    assert "registrationUrl" not in account
    state = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert state["batches"] == [] and state["jobs"] == []
