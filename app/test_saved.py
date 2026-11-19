"""담은 교육과정 화면과 프로필 저장(2026-11-20). 실행: python -m unittest app.test_saved"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.testing import open_app
from backend import chat_profiles

PASSWORD = "example profile password"


def keep_buttons(at):
    return [b for b in at.button if b.key and b.key.startswith("keep_")]


class SavedCoursesScreenTest(unittest.TestCase):
    def setUp(self):
        self.profile_db = Path(self.enterContext(TemporaryDirectory())) / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1", "USAGE_LOG": "off",
            "SKILLPATH_MOCK_PROFILE_DB": str(self.profile_db),
        }))

    def sign(self, at, nickname, *, register=False):
        at.text_input(key="auth_nickname").set_value(nickname)
        at.text_input(key="auth_password").set_value(PASSWORD)
        at.button(key="auth_register" if register else "auth_login").click().run()
        self.assertFalse(at.exception)
        self.assertIn("profile_session", at.session_state)

    def test_guest_keeps_courses_for_this_visit_and_removes_them_on_the_saved_screen(self):
        at = open_app("saved")
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "담은 교육과정")
        self.assertTrue(at.info)  # 담은 것이 없으면 안내만 보인다
        self.assertFalse(keep_buttons(at))

        at.radio(key="view").set_value("recommend").run()
        first = keep_buttons(at)[0]
        self.assertEqual(first.label, "담기")
        first.click().run()
        self.assertFalse(at.exception)
        (course,) = at.session_state["saved_courses"].values()
        self.assertEqual(set(course), {"title", "url", "level"})

        at.radio(key="view").set_value("saved").run()
        self.assertFalse(at.exception)
        self.assertFalse(at.info)
        self.assertIn(course["title"], " ".join(m.value for m in at.markdown).replace("\\", ""))
        (button,) = keep_buttons(at)
        self.assertEqual(button.label, "담아 둠")
        button.click().run()  # 이 화면에서 누르면 뺀다
        self.assertEqual(at.session_state["saved_courses"], {})
        self.assertTrue(at.info)

    def test_signing_in_stores_guest_picks_in_the_profile_and_restores_them_later(self):
        at = open_app("recommend")
        keep_buttons(at)[0].click().run()
        (course,) = at.session_state["saved_courses"].values()
        self.sign(at, "saved-course-tester", register=True)  # 비회원 때 담은 과정이 가입한 프로필로 옮겨진다
        self.assertEqual(list(at.session_state["saved_courses"].values()), [course])

        again = open_app("saved")
        self.assertTrue(again.info)
        self.sign(again, "saved-course-tester")
        self.assertEqual(list(again.session_state["saved_courses"].values()), [course])  # 다시 로그인하면 그대로
        (button,) = keep_buttons(again)
        button.click().run()  # 빼면 프로필에서도 빠진다
        self.assertFalse(again.exception)
        self.assertEqual(chat_profiles.load_saved_courses(again.session_state["profile_session"]), [])

        third = open_app("saved")
        self.sign(third, "saved-course-tester")
        self.assertEqual(third.session_state["saved_courses"], {})

    def test_a_failed_save_leaves_the_list_as_it_was(self):
        at = open_app("recommend")
        self.sign(at, "saved-course-tester", register=True)
        with patch.object(chat_profiles, "update_saved_courses", side_effect=RuntimeError("저장소 오류")):
            keep_buttons(at)[0].click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["saved_courses"], {})
        self.assertEqual(keep_buttons(at)[0].label, "담기")


if __name__ == "__main__":
    unittest.main()
