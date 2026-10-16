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


    def test_deleted_account_cookies_are_encrypted_and_recoverable(self):
        from unittest.mock import patch
        import nexora_accounts.local_profiles as local_profiles
        import nexora_accounts.proxy_store as proxy_store
        from nexora_accounts.local_profiles import LocalProfileStore
        from nexora_accounts.profile_cleanup import delete_account
        from nexora_accounts.account_backups import restore_snapshot
        from Settings.Criar_Mae.settings import utils as mae
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(local_profiles, "_default_path", lambda: root / "Nexora" / "generated_profiles.json"):
                with patch.object(proxy_store, "_default_path", lambda: root / "Nexora" / "proxies.json"):
                    with patch.object(mae, "PROFILES_DIR", root / "browser-profiles"):
                        store = LocalProfileStore()
                        profile = store.create("mae", device_mode="android")
                        folder = Path(profile["profilePath"])
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / "Cookies").write_bytes(b"secret-cookie-fixture-test")
                        result = delete_account(profile["id"])
                        archived = (store.path.parent / "deleted-account-backups" /
                                    result["backupId"] / "browser" / "Cookies.dat")
                        self.assertNotIn(b"secret-cookie-fixture-test", archived.read_bytes())
                        restored = restore_snapshot(result["backupId"])
                        self.assertEqual(restored["accountId"], profile["accountId"])
                        self.assertEqual((folder / "Cookies").read_bytes(), b"secret-cookie-fixture-test")


if __name__ == "__main__":
    unittest.main()
