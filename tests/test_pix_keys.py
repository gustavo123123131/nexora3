"""Local-only Pix key format, persistence, concurrency and agent API tests."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import servidor
import nexora_accounts.pix_key_store as mod
from nexora_accounts.pix_key_store import PixKeyStore, PixKeyError, candidates, normalize_key


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_default_path", lambda: tmp_path / "Nexora" / "pix_keys.json")
    return PixKeyStore()


def _ambiguous_value():
    """Build checksum-valid CPF with the shape of a mobile Brazilian number."""
    first = "119123456"
    digits = first
    for length in (9, 10):
        total = sum(int(digits[i]) * (length + 1 - i) for i in range(length))
        check = total * 10 % 11
        digits += str(0 if check == 10 else check)
    assert set(candidates(digits)) == {"CPF", "PHONE"}
    return digits


def test_normalization_of_four_types_and_ambiguity():
    assert normalize_key("529.982.247-25") == ("CPF", "52998224725")
    assert normalize_key("(11) 98765-4321") == ("PHONE", "11987654321")
    assert normalize_key("+55 (11) 98765-4321") == ("PHONE", "11987654321")
    assert normalize_key("USUARIO@Exemplo.COM.BR") == ("EMAIL", "usuario@exemplo.com.br")
    uuid = "123E4567-E89B-12D3-A456-426655440000"
    assert normalize_key(uuid) == ("EVP", uuid.lower())
    ambiguous = _ambiguous_value()
    with pytest.raises(PixKeyError, match="ambíguo"):
        normalize_key(ambiguous)
    assert normalize_key(ambiguous, "CPF") == ("CPF", ambiguous)
    assert normalize_key(ambiguous, "PHONE") == ("PHONE", ambiguous)


@pytest.mark.parametrize("value", [
    "", "00000000000", "111.111.111-11", "52998224724", "12345",
    "texto-aleatorio123", "abc@localhost", "abc@@example.com",
    "abc@.com", "a" * 69 + "@example.com",
    "abc@example..com", "(00) 98765-4321", "+55119876",
    "(11) 87654-3210", "01e4567-e89b-12d3-a456-426655440000",
    "123e4567-e89b-12d3-a456-42665544000x",
])
def test_invalid_values_never_enter_registry(value):
    with pytest.raises(PixKeyError):
        normalize_key(value)


def test_crud_normalized_duplicates_edit_id_and_restart(store):
    a = store.add("USUARIO@EXEMPLO.COM")["key"]
    assert a["type"] == "EMAIL"
    with pytest.raises(PixKeyError, match="já está"):
        store.add("usuario@exemplo.com")
    b = store.add("529.982.247-25")["key"]
    original_b = dict(b)
    with pytest.raises(PixKeyError, match="já está"):
        store.update(b["id"], "usuario@exemplo.com")
    assert store.list()["keys"][1] == original_b
    edited = store.update(a["id"], "(11) 98765-4321")["key"]
    assert edited["id"] == a["id"]
    assert edited["createdAt"] == a["createdAt"]
    assert edited["value"] == "11987654321"
    assert PixKeyStore(store.path).list()["keys"] == [edited, b]
    assert store.delete(b["id"]) == {"ok": True, "id": b["id"]}
    assert PixKeyStore(store.path).list()["keys"] == [edited]
    with pytest.raises(PixKeyError, match="não encontrada"):
        store.delete(b["id"])
    assert store.list()["keys"] == [edited]


def test_corrupt_file_never_replaced_on_write(store):
    store.path.parent.mkdir(parents=True)
    corrupt = b'{"version":1, "items":[{"id":"oops"}]}'
    store.path.write_bytes(corrupt)
    with pytest.raises(PixKeyError, match="inválido"):
        store.add("contato@example.com")
    assert store.path.read_bytes() == corrupt
    with pytest.raises(PixKeyError, match="inválido"):
        store.list()


def test_concurrent_writes_and_duplicate_race(store):
    entries = [f"contato{i}@exemplo.com" for i in range(35)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        values = list(pool.map(lambda v: PixKeyStore(store.path).add(v)["key"], entries))
    assert len(values) == len(entries)
    assert len({v["id"] for v in values}) == len(entries)
    assert len(PixKeyStore(store.path).list()["keys"]) == len(entries)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_concurrent_duplicate, [store.path] * 6))
    assert results.count("duplicate") == 5
    assert results.count("created") == 1


def _concurrent_duplicate(path):
    try:
        PixKeyStore(path).add("mesmo@exemplo.com")
        return "created"
    except PixKeyError:
        return "duplicate"


def test_pix_api_auth_csrf_and_separation(store):
    with TestClient(servidor.app) as client:
        assert client.get("/api/pix-keys").json() == {"keys": []}
        token = client.get("/api/state").json()["csrf_token"]
        protected = {"X-LixoCPA-CSRF": token}
        key = {"value": "User@Example.com"}
        assert client.post("/api/pix-keys/add", json=key).status_code == 403
        created = client.post("/api/pix-keys/add", json=key, headers=protected)
        assert created.status_code == 200, created.text
        item = created.json()["key"]
        assert item["value"] == "user@example.com"
        assert client.post("/api/pix-keys/add", json=key, headers=protected).status_code == 409
        assert client.get("/api/pix-keys").json()["keys"] == [item]
        assert client.post("/api/pix-keys/update", json={
            "id": item["id"], "value": "529.982.247-25"
        }).status_code == 403
        changed = client.post("/api/pix-keys/update", json={
            "id": item["id"], "value": "529.982.247-25"
        }, headers=protected)
        assert changed.status_code == 200
        assert changed.json()["key"]["id"] == item["id"]
        assert changed.json()["key"]["type"] == "CPF"
        deleted = client.post("/api/pix-keys/delete", json={
            "id": item["id"]
        }, headers=protected)
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert client.get("/api/pix-keys").json() == {"keys": []}
        assert client.get("/api/pix-keys", headers={"Host": "attacker.example"}).status_code == 403
        # No Pix operation reads or writes the existing proxy/account registries.
        assert not (store.path.parent / "proxies.json").exists()
        assert not (store.path.parent / "generated_profiles.json").exists()


def test_type_mismatch_and_no_value_in_error(store):
    secret = "private-key-not-a-real-pix"
    for body in [{"value": secret}, {"value": "52998224725", "type": "EMAIL"}]:
        with pytest.raises(PixKeyError) as err:
            store.add(body["value"], body.get("type"))
        assert secret not in str(err.value)
    assert store.list()["keys"] == []


def test_phone_registry_never_includes_country_prefix_and_rejects_duplicates(store):
    phone = store.add("(11) 98765-4321")["key"]
    assert phone["value"] == "11987654321"
    assert phone["type"] == "PHONE"
    with pytest.raises(PixKeyError, match="já está"):
        store.add("+55 (11) 98765-4321")
    assert PixKeyStore(store.path).list()["keys"] == [phone]
    assert "+55" not in store.path.read_text(encoding="utf-8")


def test_legacy_e164_phone_migrates_without_replacing_account_ids(store):
    phone = store.add("11987654321")["key"]
    email = store.add("contato@example.com")["key"]
    before = json.loads(store.path.read_text(encoding="utf-8"))
    # Simulate the file written by the previous release.
    before["items"][0]["value"] = "+5511987654321"
    store.path.write_text(json.dumps(before), encoding="utf-8")
    after = PixKeyStore(store.path).list()["keys"]
    assert after == [phone, email]
    assert json.loads(store.path.read_text(encoding="utf-8"))["items"] == [phone, email]
    assert PixKeyStore(store.path).list()["keys"] == after
    with pytest.raises(PixKeyError, match="já está"):
        store.add("+5511987654321")


def test_invalid_preexisting_phone_file_is_never_overwritten(store):
    saved = store.add("11987654321")["key"]
    corrupt = json.loads(store.path.read_text(encoding="utf-8"))
    corrupt["items"][0]["value"] = "+5511987654321"
    corrupt["items"].append(dict(saved, id="invalid-uuid"))
    original = json.dumps(corrupt).encode("utf-8")
    store.path.write_bytes(original)
    with pytest.raises(PixKeyError, match="inválido"):
        store.list()
    assert store.path.read_bytes() == original
