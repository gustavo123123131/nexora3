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


if __name__ == "__main__":
    unittest.main()
