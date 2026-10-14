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
    path = tmp_path / "contas.sqlite"
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
    with pytest.raises(AccountError):
        repo.set_stage(child["id"], "completed", "claimed-via-ui")
    repo.set_stage(child["id"], "pending", "review")
    assert repo.account(child["id"])["status"] == "pending"
    repo.delete(mother["id"])
    assert repo.account(child["id"])["parentId"] is None
    # Persistence after reopening (no credentials).
    assert len(AccountsRepository(path).accounts()) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM account_audit").fetchone()[0] >= 5


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
                        AccountsService(tmp_path / "api.sqlite", start_browser=False))
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
