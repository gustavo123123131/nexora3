"""Tests use simulated browser elements, never an external platform."""
from nexora_accounts.registration_autofill import autofill_existing_registration


class Field:
    def __init__(self, present=True, kind="text"):
        self.present, self.kind, self.fills, self.clicks = present, kind, [], 0
        self.first = self

    def is_visible(self):
        return self.present

    def get_attribute(self, key):
        return self.kind if key == "type" else None

    def fill(self, value, timeout=None):
        self.fills.append(value)

    def click(self, timeout=None):
        self.clicks += 1


class Page:
    def __init__(self, url, fields):
        self.url, self.fields = url, fields

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def locator(self, selector):
        return self.fields.get(selector, Field(False))


class Repo:
    def __init__(self, data, target):
        self.data, self.target, self.stages = data, target, []

    def account(self, key):
        return {"targetUrl": self.target, "profileData": self.data}

    def set_stage(self, key, status, stage):
        self.stages.append((key, status, stage))


def elements():
    return {f"input[data-input-name='{key}']": Field()
            for key in ("account", "userpass", "confirmPassword", "realName")}


def profile(**extra):
    return {"username": "joana_titular", "password": "Senha!Teste12345",
            "fullName": "Pessoa Autorizada", **extra}


def test_real_profile_autofills_but_never_submits():
    fields = elements()
    repo = Repo(profile(isTestData=False, ownerConfirmed=True),
                "https://example.org/register")
    result = autofill_existing_registration(
        Page("https://example.org/register", fields), {"profile_record_id": "id"}, repo)
    assert result["status"] == "filled"
    assert fields["input[data-input-name='account']"].fills == ["joana_titular"]
    assert fields["input[data-input-name='confirmPassword']"].fills == ["Senha!Teste12345"]
    assert all(field.clicks == 0 for field in fields.values())
    assert repo.stages == [("id", "pending", "review")]


def test_test_identity_cannot_autofill_remote_form():
    fields = elements()
    repo = Repo(profile(isTestData=True), "https://example.org/register")
    result = autofill_existing_registration(
        Page("https://example.org/register", fields), {"profile_record_id": "id"}, repo)
    assert result["status"] == "blocked"
    assert not fields["input[data-input-name='account']"].fills
    assert repo.stages == [("id", "local", "test-data-blocked")]


def test_test_data_can_prefill_local_demo():
    fields = elements()
    repo = Repo(profile(isTestData=True), "http://127.0.0.1:9000/register")
    result = autofill_existing_registration(
        Page("http://127.0.0.1:9000/register", fields), {"profile_record_id": "id"}, repo)
    assert result["status"] == "filled"


def test_cross_origin_never_receives_profile_values():
    fields = elements()
    repo = Repo(profile(isTestData=False, ownerConfirmed=True),
                "https://example.org/register")
    result = autofill_existing_registration(
        Page("https://other.example.org/register", fields),
        {"profile_record_id": "id"}, repo)
    assert result["status"] == "blocked"
    assert not fields["input[data-input-name='account']"].fills


def test_unverified_cpf_not_prefilled():
    fields = elements()
    fields["input[data-input-name='cpf']"] = Field()
    repo = Repo(profile(isTestData=False, ownerConfirmed=True,
                        cpf="000.000.000-00", cpfVerified=False),
                "https://example.org/register")
    autofill_existing_registration(
        Page("https://example.org/register", fields), {"profile_record_id": "id"}, repo)
    assert not fields["input[data-input-name='cpf']"].fills
