import unittest
from unittest.mock import patch

from backend import profile_auth_limits as limits


class LoginAttemptTest(unittest.TestCase):
    def test_window_and_success_reset(self):
        self.addCleanup(limits.clear_login_attempts, "example")
        with patch.object(limits, "monotonic", return_value=100) as clock:
            for _ in range(limits.MAX_ATTEMPTS):
                self.assertTrue(limits.reserve_login_attempt("example"))
            self.assertFalse(limits.reserve_login_attempt("example"))
            clock.return_value += limits.WINDOW_SECONDS
            self.assertTrue(limits.reserve_login_attempt("example"))
            limits.clear_login_attempts("example")
            for _ in range(limits.MAX_ATTEMPTS):
                self.assertTrue(limits.reserve_login_attempt("example"))


if __name__ == "__main__":
    unittest.main()
