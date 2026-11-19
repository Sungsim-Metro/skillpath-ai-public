"""처음 접속했을 때의 로그인·회원가입 화면 확인(가짜 모드, 2026-11-20). 실행: python -m unittest app.test_entry"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.testing import open_app
from backend import chat_profiles

PASSWORD = "correct horse battery staple"


def _submit(at, button: str, nickname: str, password: str = PASSWORD):
    at.text_input(key="auth_nickname").set_value(nickname)
    at.text_input(key="auth_password").set_value(password)
    at.button(key=button).click()
    return at.run()


class EntryScreenTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1", "USAGE_LOG": "off",
            "SKILLPATH_MOCK_PROFILE_DB": str(Path(folder.name) / "profiles.sqlite3"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_the_entry_screen_shows_before_entering(self):
        for view in (None, "gap", "recommend"):
            at = open_app(view, entered=False)
            self.assertFalse(at.exception)
            self.assertEqual(at.title[0].value, "skillpath-ai")
            self.assertTrue(at.button(key="guest_enter"))
            self.assertFalse(at.chat_input)
            self.assertFalse(at.sidebar.radio)
            self.assertFalse(at.dataframe)
        self.assertNotIn("profile_session", at.session_state)

    def test_guest_browses_every_screen_without_touching_profiles(self):
        with mock.patch.object(chat_profiles, "load_profile", wraps=chat_profiles.load_profile) as read, \
             mock.patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as write:
            at = open_app("gap", entered=False)
            at.button(key="guest_enter").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.title[0].value, "부족 역량")  # 주소로 연 화면이 그대로 열린다
            for view in ("demand", "recommend", "connect", "chat"):
                at.radio(key="view").set_value(view).run()
                self.assertFalse(at.exception, view)
            at.chat_input[0].set_value("프로젝트에서 제가 Docker로 API를 배포했어요.").run()
            at.chat_input[0].set_value("방금 말한 Docker 배포 경험을 프로필에 저장해줘.").run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.chat_message), 4)
            read.assert_not_called()
            write.assert_not_called()
        self.assertNotIn("profile_session", at.session_state)
        self.assertTrue(at.sidebar.button(key="auth_login"))  # 들어온 뒤에도 사이드바에서 로그인할 수 있다

    def test_first_entry_shows_the_guide_once_and_it_can_be_reopened_only_when_asked(self):
        at = open_app(entered=False)
        self.assertNotIn("tour_open", at.session_state)
        at.button(key="guest_enter").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state["tour_open"])
        self.assertTrue(at.checkbox(key="tour_hide").value)  # '다시 보지 않기'가 처음부터 체크돼 있다
        self.assertTrue(at.button(key="tour_prev").disabled)
        steps = 1
        while [b for b in at.button if b.key == "tour_next"]:
            at.button(key="tour_next").click().run()
            self.assertFalse(at.exception)
            steps += 1
        self.assertEqual(steps, 5)
        at.button(key="tour_done").click().run()
        self.assertFalse(at.session_state["tour_open"])
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("tour_")])  # 닫으면 다시 뜨지 않는다
        at.radio(key="view").set_value("gap").run()
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("tour_")])

        again = open_app(entered=False)
        again.button(key="guest_enter").click().run()
        again.checkbox(key="tour_hide").set_value(False).run()
        again.button(key="tour_skip").click().run()  # 체크를 풀고 닫으면 사이드바에서 다시 열 수 있다
        self.assertFalse(again.session_state["tour_open"])
        again.sidebar.button(key="tour_reopen").click().run()
        self.assertTrue(again.session_state["tour_open"])
        self.assertTrue([b for b in again.button if b.key == "tour_next"])

        self.assertNotIn("tour_open", open_app().session_state)  # 화면 테스트처럼 첫 화면을 거치지 않으면 뜨지 않는다

    def test_sign_up_and_login_enter_the_chat_and_wrong_passwords_do_not(self):
        at = _submit(open_app(entered=False), "entry_register", "예시 사용자")
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["profile_session"].nickname, "예시 사용자")
        self.assertEqual(at.session_state["view"], "chat")  # 들어오면 첫 화면은 학습 상담
        self.assertEqual(len(at.chat_input), 1)
        self.assertEqual(at.session_state["auth_password"], "")  # 적었던 비밀번호가 세션에 남지 않는다
        self.assertTrue(any("예시 사용자" in t.value for t in at.sidebar.text))

        again = _submit(open_app(entered=False), "entry_register", "예시 사용자", "another password")  # 이미 있는 닉네임
        self.assertTrue(again.error)
        self.assertNotIn("profile_session", again.session_state)
        again = _submit(again, "entry_login", "예시 사용자", "another password")
        self.assertTrue(again.error)
        self.assertFalse(again.chat_input)
        self.assertEqual(again.session_state["auth_password"], "")
        again = _submit(again, "entry_login", "예시 사용자")
        self.assertFalse(again.exception)
        self.assertEqual(again.session_state["profile_session"].nickname, "예시 사용자")
        self.assertFalse(again.error)

    def test_logout_keeps_browsing_as_a_guest_without_the_previous_chat(self):
        at = _submit(open_app(entered=False), "entry_register", "예시 사용자")
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        self.assertEqual(len(at.chat_message), 2)
        at.sidebar.button(key="auth_logout").click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("profile_session", at.session_state)
        self.assertEqual(len(at.chat_input), 1)  # 로그인 화면으로 돌아가지 않고 비회원으로 계속 본다
        self.assertFalse(at.chat_message)        # 앞사람의 상담은 지워진다


if __name__ == "__main__":
    unittest.main()
