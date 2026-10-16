"""Windows-only local DPAPI round-trip, no network or real credentials."""
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from nexora_accounts.credential_vault import (
    is_available, write_secret, read_secret, delete_secret,
)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class WindowsDpapiTests(unittest.TestCase):
    def test_password_protected_and_restored_without_plaintext_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "generated_profiles.json"
            account_id = str(uuid.uuid4())
            password = "local-test-secret-not-for-real-use"
            ref = write_secret(home, account_id, password)
            vault = home.parent / "credentials" / (account_id + ".dpapi")
            self.assertEqual(ref, "dpapi:" + account_id)
            self.assertNotIn(password.encode(), vault.read_bytes())
            self.assertEqual(read_secret(home, ref), password)
            delete_secret(home, ref)
            self.assertFalse(vault.exists())


    def test_proxy_password_stays_out_of_json_and_survives_reload(self):
        from nexora_accounts.proxy_store import ProxyStore
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "proxies.json"
            store = ProxyStore(path)
            item = store.save_line("proxy.example.test:8312:demo:secret-proxy-test")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-proxy-test", raw)
            self.assertIn("passwordRef", raw)
            self.assertTrue(ProxyStore(path).list()["proxies"][0]["hasPassword"])
            assigned = ProxyStore(path).assign(1)
            self.assertEqual(assigned[0]["password"], "secret-proxy-test")
            self.assertEqual(assigned[0]["id"], item["id"])


if __name__ == "__main__":
    unittest.main()
