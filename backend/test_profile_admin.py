import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend import chat_profiles, profile_admin


class LegacyEnrollmentTest(unittest.TestCase):
    def test_verified_existing_owner_can_sign_in_after_terminal_enrollment(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "profiles.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE profiles (nickname TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.execute("INSERT INTO profiles VALUES (?, ?)", ("example", json.dumps({"levels": {"C8-1": 2}})))
            passphrase = "test-only enrollment phrase"
            with (patch.dict(os.environ, {"BEDROCK_MOCK": "1", "SKILLPATH_MOCK_PROFILE_DB": str(path)}),
                  patch.object(profile_admin.sys.stdin, "isatty", return_value=True),
                  patch("builtins.input", return_value="example"), patch("builtins.print"),
                  patch.object(profile_admin, "getpass", return_value=passphrase)):
                profile_admin.enroll()
                session = chat_profiles.sign_in("example", passphrase)
                self.assertEqual(chat_profiles.load_profile(session)["levels"], {"C8-1": 2})

    def test_requires_private_terminal_and_matching_input_before_write(self):
        with (patch.object(profile_admin.sys.stdin, "isatty", return_value=False) as tty,
              patch("builtins.input", return_value="example"),
              patch.object(profile_admin, "getpass", side_effect=["first", "second"]) as prompt,
              patch.object(profile_admin, "set_legacy_password") as enroll):
            with self.assertRaises(SystemExit):
                profile_admin.enroll()
            prompt.assert_not_called()
            tty.return_value = True
            with self.assertRaises(SystemExit):
                profile_admin.enroll()
            enroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
