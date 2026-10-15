"""Regression tests: generated profile data and local-only demo submit.

All tests run offline; no third-party APIs or KYC requests.
"""
import json
from nexora_accounts.profile_data import make_password, make_profile_data
from nexora_accounts.registration_submit import _real_values, request_demo_submit
from nexora_accounts.service import AccountsRepository
from nexora_accounts.registration_submit import RegistrationWatcher


class Repo:
    def __init__(self, account):
        self.account_record = dict(account)
        self.events = []

    def account(self, key):
        assert key == self.account_record["id"]
        return dict(self.account_record)

    def set_stage(self, key, status, stage, http_status=None, error_code=None, duration_ms=None):
        assert key == self.account_record["id"]
        self.account_record.update(status=status, stage=stage)
        self.events.append((status, stage))


class Locator:
    def __init__(self, visible=True):
        self.first = self
        self.visible = visible
        self.clicks = 0

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicks += 1


class Page:
    def __init__(self, url):
        self.url = url
        self.button = Locator()

    def on(self, event, callback):
        pass

    def locator(self, selector):
        if selector == "#insideRegisterSubmitClick":
            return self.button
        return Locator(False)

    def is_closed(self):
        return False


class Clock:
    def __call__(self):
        return 100.0


def test_new_profiles_have_usable_generated_usernames_and_passwords():
    profiles = [make_profile_data() for _ in range(30)]
    assert len({x["username"] for x in profiles}) == len(profiles)
    assert len({x["password"] for x in profiles}) == len(profiles)
    for item in profiles:
        assert not item["username"].startswith("teste")
        assert len(item["password"]) == 16
        assert item["email"] == item["username"] + "@example.test"
        assert item["isTestData"] is True
    assert len(make_password()) == 16


def test_existing_profile_credentials_auto_reused_without_retyping():
    generated = make_profile_data()
    user_input = {
        "fullName": "Titular Autorizado",
        "email": "pessoa@dominio-proprio.net",
        "phone": "",
    }
    values = _real_values(user_input, generated)
    assert values["username"] == generated["username"]
    assert values["password"] == generated["password"]
    assert values["confirmPassword"] == generated["password"]
    assert values["fullName"] == "Titular Autorizado"
    assert values["email"] == "pessoa@dominio-proprio.net"


def test_error_message_tells_which_requirement_failed():
    generated = {**make_profile_data(), "password": "curta"}
    user_input = {"fullName": "Titular Autorizado", "email": "pessoa@dominio-proprio.net"}
    try:
        _real_values(user_input, generated)
    except ValueError as exc:
        assert "Senha curta" in str(exc)
        assert "Username ou senha" not in str(exc)
    else:
        assert False, "Weak password must fail clearly"


def test_old_local_test_passwords_upgrade_when_repository_reopens(tmp_path):
    db = tmp_path / "accounts.json"
    repo = AccountsRepository(db)
    plan = repo.create_launch_profiles(
        "mae", "desktop", ["http://127.0.0.1:9090/register"], 1, tmp_path / "profiles"
    )[0]
    account_id = plan["profile_record_id"]
    saved = json.loads(db.read_text(encoding="utf-8"))
    saved["accounts"][0]["profileData"]["password"] = "aaAB#0123"
    db.write_text(json.dumps(saved), encoding="utf-8")
    new = AccountsRepository(db)
    account = new.account(account_id)
    assert len(account["profileData"]["password"]) == 16
    assert account["profileData"]["isTestData"] is True
    assert account["stage"] == "profile-created"


def test_demo_submission_queues_only_local_pages_without_manual_values():
    account = {
        "id": "demo-test",
        "targetUrl": "http://127.0.0.1:9087/register",
        "profileData": {"isTestData": True},
        "status": "pending",
        "stage": "review",
    }
    repo = Repo(account)
    page = Page(account["targetUrl"])
    watcher = RegistrationWatcher(page, {"profile_record_id": "demo-test"},
                                  repository=repo, clock=Clock(), success_selector="")
    assert request_demo_submit("demo-test", repo) is True
    assert request_demo_submit("demo-test", repo) is False
    watcher.tick()
    assert page.button.clicks == 1
    assert repo.account_record["status"] == "pending"


def test_auto_demo_submits_never_allow_external_sites():
    account = {
        "id": "external-test",
        "targetUrl": "https://site-real.example.org/register",
        "profileData": {"isTestData": True},
        "status": "pending",
        "stage": "review",
    }
    repo = Repo(account)
    assert request_demo_submit("external-test", repo) is False
    assert repo.events == []
