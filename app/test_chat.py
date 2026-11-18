"""학습 상담 화면의 호출 범위와 세션 분리를 확인한다."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from backend import api, chat_profiles
from backend.jobs import JOBS

MAIN = str(Path(__file__).with_name("main.py"))


class ChatScreenTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1"}))

    def open_screen(self, view="chat"):
        at = AppTest.from_file(MAIN, default_timeout=60)
        at.query_params["view"] = view
        at.run()
        self.assertFalse(at.exception)
        return at

    def test_chat_entry_and_experience_do_not_run_dashboard(self):
        with (patch.object(api, "get_demand", wraps=api.get_demand) as demand,
              patch.object(api, "get_gap", wraps=api.get_gap) as gap,
              patch.object(api, "get_recommendations", wraps=api.get_recommendations) as recs):
            at = self.open_screen()
            self.assertEqual(len(at.chat_input), 1)
            self.assertFalse(at.tabs)
            at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
            at.chat_input[0].set_value("지금까지 말한 내 경험을 정리해줘.").run()
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.chat_message), 4)
            self.assertIn("Docker", at.chat_message[-1].text[0].value)
            demand.assert_not_called()
            gap.assert_not_called()
            recs.assert_not_called()

    def test_gap_and_courses_run_only_on_the_corresponding_submission(self):
        with (patch.object(api, "get_gap", wraps=api.get_gap) as gap,
              patch.object(api, "get_recommendations", wraps=api.get_recommendations) as recs):
            at = self.open_screen()
            at.chat_input[0].set_value("클라우드 엔지니어 기준으로 뭐가 부족해?").run()
            self.assertEqual(gap.call_count, 1)
            recs.assert_not_called()
            at.run()
            self.assertEqual(gap.call_count, 1)
            self.assertFalse(at.get("link_button"))
            at.chat_input[0].set_value("그중 먼저 들을 교육과정을 추천해줘.").run()
            self.assertTrue(at.get("link_button"))
            self.assertEqual(recs.call_count, 1)
            count = gap.call_count
            at.run()
            self.assertEqual(gap.call_count, count)
            self.assertEqual(recs.call_count, 1)
            self.assertFalse(at.exception)

    def test_only_explicit_skills_travel_between_screens(self):
        at = self.open_screen("dashboard")
        at.sidebar.button_group(key="owned").select("Docker").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(at.multiselect(key="chat_owned").value, ["Docker"])
        at.multiselect(key="chat_owned").set_value(["Git", "Kotlin"]).run()
        at.radio(key="view").set_value("dashboard").run()
        self.assertEqual(set(at.sidebar.button_group(key="owned").value), {"Git"})
        self.assertEqual(len(at.tabs), 4)
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(set(at.multiselect(key="chat_owned").value), {"Git", "Kotlin"})
        self.assertFalse(at.exception)

    def test_deselected_then_reselected_dashboard_example_is_explicit(self):
        at = self.open_screen("dashboard")
        at.sidebar.button_group(key="owned").unselect("Python").run()
        at.sidebar.button_group(key="owned").select("Python").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(at.multiselect(key="chat_owned").value, ["Python"])

    def test_job_change_preserves_chosen_skills_outside_new_dashboard_demand(self):
        get_demand = api.get_demand

        def demand_for_job(job):
            result = get_demand(job)
            if job == "test_job":
                result["skills"] = [s for s in result["skills"] if s["skill"] != "Docker"]
            return result

        with patch.dict(JOBS, {"test_job": "테스트 직무"}), patch.object(api, "get_demand", demand_for_job):
            at = self.open_screen("dashboard")
            at.sidebar.button_group(key="owned").select("Docker").run()
            at.selectbox(key="job").set_value("test_job").run()
            self.assertNotIn("Docker", at.sidebar.button_group(key="owned").options)
            at.radio(key="view").set_value("chat").run()
            self.assertEqual(at.selectbox(key="job").value, "test_job")
            self.assertEqual(at.multiselect(key="chat_owned").value, ["Docker"])
            self.assertFalse(at.exception)

    def test_nickname_and_reruns_do_not_replay_or_leak_messages(self):
        at = self.open_screen()
        echo = "![text](https://example.com/image)"
        with patch("backend.chat.handle_message", return_value=SimpleNamespace(text=echo, courses=[])) as handle:
            at.chat_input[0].set_value(echo).run()
            at.text_input(key="chat_nickname").set_value("첫째").run()
            self.assertEqual([m.text[0].value for m in at.chat_message], [echo, echo])
            self.assertFalse(any(echo in m.value for m in at.markdown))
            at.multiselect(key="chat_owned").set_value(["Docker"]).run()
            at.chat_input[0].set_value("Docker 경험을 저장해줘").run()
            self.assertEqual(handle.call_args.kwargs["nickname"], "첫째")
            self.assertEqual(handle.call_args.kwargs["sidebar_skills"], ["Docker"])
            first_state = handle.call_args.args[0]
            at.radio(key="view").set_value("dashboard").run()
            at.radio(key="view").set_value("chat").run()
            self.assertEqual(len(at.chat_message), 4)
            self.assertEqual(at.text_input(key="chat_nickname").value, "첫째")
            at.text_input(key="chat_nickname").set_value("둘째").run()
            self.assertFalse(at.chat_message)
            self.assertEqual(at.multiselect(key="chat_owned").value, [])
            self.assertEqual(handle.call_count, 2)
            at.chat_input[0].set_value("내 상태를 정리해줘").run()
            self.assertIsNot(handle.call_args.args[0], first_state)
            self.assertEqual(handle.call_args.kwargs["nickname"], "둘째")
            at.text_input(key="chat_nickname").set_value("").run()
            self.assertFalse(at.chat_message)
            self.assertFalse(at.exception)

    def test_saving_a_guest_experience_after_entering_a_nickname(self):
        folder = self.enterContext(TemporaryDirectory())
        database = Path(folder) / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {"SKILLPATH_MOCK_PROFILE_DB": str(database)}))
        at = self.open_screen()
        at.chat_input[0].set_value("프로젝트에서 제가 Docker로 API를 배포했어요.").run()
        at.text_input(key="chat_nickname").set_value("상담 예시").run()
        with patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as save:
            self.assertFalse(database.exists())
            at.chat_input[0].set_value("방금 말한 Docker 배포 경험을 프로필에 저장해줘.").run()
            self.assertIn("저장했습니다", at.chat_message[-1].text[0].value)
            at.run()
            self.assertEqual(save.call_count, 1)
        profile = chat_profiles.load_profile("상담 예시")
        self.assertFalse(profile["skills"])
        self.assertEqual(len(profile["experiences"]), 1)
        self.assertIn("Docker", profile["experiences"][0])
        reopened = self.open_screen()
        reopened.text_input(key="chat_nickname").set_value("상담 예시").run()
        with patch.object(api, "get_gap", wraps=api.get_gap) as gap:
            reopened.chat_input[0].set_value("내 경험 기준으로 뭐가 부족해?").run()
            gap.assert_called_once_with("cloud_engineer", ["Docker"])
        self.assertEqual(chat_profiles.load_profile("상담 예시"), profile)

    def test_returned_course_links_have_plain_text_details(self):
        at = self.open_screen()
        course = {"title": "[예시 과정]", "url": "https://example.com/course", "match": "uncertain",
                  "level": "기초", "prerequisites": "없음", "reason": "과정에서 배포 실습을 다룹니다."}
        invalid = {**course, "url": "javascript:alert(1)"}
        reply = SimpleNamespace(text="검토한 교육과정입니다.", courses=[course, invalid])
        with patch("backend.chat.handle_message", return_value=reply):
            at.chat_input[0].set_value("과정 추천해줘").run()
            at.chat_input[0].set_value("과정을 다시 보여줘").run()
        self.assertFalse(at.exception)
        self.assertEqual([item.proto.url for item in at.get("link_button")], [course["url"], course["url"]])
        details = " ".join(item.value for item in at.chat_message[-1].text)
        for expected in (course["title"], course["reason"], "불확실"):
            self.assertIn(expected, details)


if __name__ == "__main__":
    unittest.main()
