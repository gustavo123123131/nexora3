"""Offline tests: never submit to external servers."""
import uuid

import pytest

from nexora_accounts.registration_submit import RegistrationWatcher, request_submit


class Repo:
    def __init__(self, account):
        self.account_record = dict(account)
        self.events = []

    def account(self, key):
        assert key == self.account_record["id"]
        return dict(self.account_record)

    def set_stage(self, key, status, stage, http_status=None):
        assert key == self.account_record["id"]
        self.account_record.update(status=status, stage=stage)
        self.events.append({"status": status, "stage": stage, "http": http_status})


class Locator:
    def __init__(self, present=True):
        self.first = self
        self.present = present
        self.clicks = 0

    def is_visible(self):
        return self.present

    def click(self, timeout=None):
        self.clicks += 1


class Page:
    def __init__(self, url):
        self.url = url
        self.closed = False
        self.callbacks = {}
        self.button = Locator()
        self.success = Locator(False)

    def on(self, event, callback):
        self.callbacks[event] = callback

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        if selector == "#insideRegisterSubmitClick":
            return self.button
        if selector == "#registration-success":
            return self.success
        return Locator(False)

    def response(self, status):
        class Request:
            method = "POST"

        class Response:
            url = "https://api.example.org/hall/api/member/register"
            request = Request()

            def __init__(self, status):
                self.status = status

        self.callbacks["response"](Response(status))


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


def valid_account():
    return {
        "id": str(uuid.uuid4()),
        "targetUrl": "https://example.org/register",
        "profileData": {
            "isTestData": False,
            "ownerConfirmed": True,
            "password": "SEGREDO-QUE-NAO-DEVE-APARECER-EM-LOG",
        },
        "stage": "review",
        "status": "pending",
    }


def test_request_needs_confirmation_and_blocks_test_identities():
    account = valid_account()
    repo = Repo(account)
    with pytest.raises(Exception):
        request_submit(account["id"], False, repo)
    repo.account_record["profileData"]["isTestData"] = True
    with pytest.raises(Exception):
        request_submit(account["id"], True, repo)
    assert not repo.events


def test_click_sent_once_and_http_200_not_mistaken_for_success():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key, "idx": 2},
                                  repository=repo, clock=clock,
                                  success_selector="#registration-success")
    assert request_submit(key, True, repo)["queued"]
    assert request_submit(key, True, repo)["duplicate"]
    watcher.tick()
    assert page.button.clicks == 1
    assert repo.account_record["status"] == "pending"
    page.response(200)
    clock.value = 101
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "provider-response-unverified"
    assert repo.events[-1]["http"] == 200
    assert all("SEGREDO" not in str(event) for event in repo.events)
    clock.value = 102
    page.success.present = True
    watcher.tick()
    assert repo.account_record["status"] == "completed"
    assert repo.account_record["stage"] == "provider-confirmed"
    assert page.button.clicks == 1


def test_http_error_is_pending_until_provider_evidence():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, clock=clock,
                                  success_selector="#registration-success")
    request_submit(key, True, repo)
    watcher.tick()
    page.response(422)
    clock.value = 102
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "provider-http-error"


def test_unexpected_origin_blocks_submission():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page = Page("https://wrong.example.org/register")
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, success_selector="")
    request_submit(key, True, repo)
    watcher.tick()
    assert page.button.clicks == 0
    assert repo.account_record["stage"] == "origin-changed"


def test_timeout_keeps_account_pending_and_does_not_retry():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, clock=clock, success_selector="")
    request_submit(key, True, repo)
    watcher.tick()
    clock.value = 150
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "timeout-unverified"
    clock.value = 152
    watcher.tick()
    assert page.button.clicks == 1


def test_json_events_are_bounded_and_exclude_credentials(tmp_path):
    from nexora_accounts.service import AccountsRepository
    from Settings.device_modes import profile_directory

    repo = AccountsRepository(tmp_path / "accounts.json")
    plan = repo.create_launch_profiles(
        "mae", "desktop", ["https://example.org/register"], 1, tmp_path / "profiles"
    )[0]
    key = plan["profile_record_id"]
    for _ in range(40):
        repo.set_stage(key, "pending", "provider-response-unverified", http_status=200)
    row = repo.account(key)
    assert len(row["registrationLog"]) == 30
    assert all(event == {"at": event["at"], "stage": "provider-response-unverified",
                         "httpStatus": 200} for event in row["registrationLog"])
