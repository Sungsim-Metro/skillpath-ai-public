"""학습 상담 화면의 호출 범위와 세션 분리를 확인한다."""
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ai.chat import Turn
from backend import api, chat_profiles
from backend.jobs import JOBS

MAIN = str(Path(__file__).with_name("main.py"))


class ChatScreenTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1", "USAGE_LOG": "off",
        }))

    def open_screen(self, view="chat"):
        at = AppTest.from_file(MAIN, default_timeout=60)
        at.query_params["view"] = view
        at.run()
        self.assertFalse(at.exception)
        return at

    def test_token_usage_counts_retries_and_failed_turns_only_for_this_chat(self):
        log = Path(self.enterContext(TemporaryDirectory())) / "usage.jsonl"
        self.enterContext(patch.dict(os.environ, {"USAGE_LOG": str(log), "ADMIN_KEY": "test-key"}))
        at = self.open_screen()
        other = self.open_screen()
        session_id = at.session_state["chat_conversation"].usage.session_id
        self.assertNotEqual(session_id, other.session_state["chat_conversation"].usage.session_id)

        def response(body, inputs, outputs):
            return body, "end_turn", {"inputTokens": inputs, "outputTokens": outputs,
                                      "totalTokens": inputs + outputs}

        replies = [
            response("invalid", 10, 1),
            response(json.dumps(asdict(Turn(action="summary"))), 20, 2),
            response("invalid", 30, 3),
            response("invalid", 40, 4),
        ]
        with (patch("ai.chat.guard_input", return_value=True),
              patch("ai.bedrock.is_mock", return_value=False),
              patch("ai.bedrock._call", side_effect=replies) as call):
            at.chat_input[0].set_value("내 경험을 정리해줘").run()
            self.assertIn("보유 기술", at.chat_message[-1].text[0].value)
            self.assertEqual(at.metric[0].value, "33")
            at.chat_input[0].set_value("다시 정리해줘").run()
            self.assertIn("해석하지 못했습니다", at.chat_message[-1].text[0].value)
            self.assertEqual(at.metric[0].value, "110")
            self.assertTrue(any(f"입력 100 · 출력 10 · 세션 ID `{session_id}`" in c.value for c in at.caption))
            at.run()
            other.run()
            self.assertEqual(at.metric[0].value, "110")
            self.assertEqual(other.metric[0].value, "0")
            self.assertEqual(call.call_count, 4)

        at.text_input(key="chat_nickname").set_value("처음 닉네임").run()
        at.radio(key="view").set_value("dashboard").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(at.metric[0].value, "110")
        self.assertEqual(session_id, at.session_state["chat_conversation"].usage.session_id)
        at.text_input(key="chat_nickname").set_value("새 닉네임").run()
        self.assertEqual(at.metric[0].value, "0")
        self.assertNotEqual(session_id, at.session_state["chat_conversation"].usage.session_id)

        root = str(Path(MAIN).parent.parent)
        page = "\n".join(["import sys", f"sys.path.insert(0, {root!r})", "from app import admin", "admin.render()"])
        admin = AppTest.from_string(page, default_timeout=60).run()  # 관리자 화면의 토큰·비용 탭
        self.assertFalse(admin.exception)
        session_table = next(
            frame.value for frame in admin.dataframe if "세션 ID" in frame.value.columns
        )
        self.assertEqual(session_table["세션 ID"].tolist(), [session_id])
        row = session_table.iloc[0]
        self.assertEqual((row["입력 토큰"], row["출력 토큰"], row["합계 토큰"], row["재시도"]), (100, 10, 110, 2))

    def test_in_progress_chat_starts_tracking_with_existing_history(self):
        at = self.open_screen()
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        messages = [item.text[0].value for item in at.chat_message]
        del at.session_state["chat_conversation"].usage

        at.run()
        self.assertFalse(at.exception)
        self.assertEqual([item.text[0].value for item in at.chat_message], messages)
        self.assertEqual(at.metric[0].value, "0")
        session_id = at.session_state["chat_conversation"].usage.session_id
        at.run()
        self.assertEqual(at.session_state["chat_conversation"].usage.session_id, session_id)

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

    def test_answers_take_a_rating_and_two_recommendations_take_a_choice(self):
        """비교 실험: 답마다 평가를 받고, 추천 구조가 둘이면 A/B 로 나란히 보여 준다(기록에는 내용이 없다)."""
        from backend import experiments

        off = self.open_screen()
        off.chat_input[0].set_value("클라우드 엔지니어 기준으로 뭐가 부족해?").run()
        self.assertFalse(off.get("feedback"))                       # 실험이 꺼져 있으면 평가 위젯이 없다
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "feedback.jsonl"
            with patch.dict(os.environ, {"AB_STRUCTURES": "single,reviewed", "FEEDBACK_LOG": str(log)}):
                at = self.open_screen()
                at.chat_input[0].set_value("클라우드 엔지니어 기준으로 뭐가 부족해?").run()
                self.assertFalse(at.exception)
                self.assertEqual(len(at.get("feedback")), 1)        # 답 하나에 평가 위젯 하나
                self.assertFalse(at.radio(key="view") is None)
                self.assertFalse([r for r in at.radio if r.key and r.key.startswith("ab_")])
                at.chat_input[0].set_value("그중 먼저 들을 교육과정을 추천해줘.").run()
                self.assertFalse(at.exception)
                choice = [r for r in at.radio if r.key and r.key.startswith("ab_")]
                self.assertEqual(len(choice), 1)
                self.assertEqual([m.value for m in at.markdown if m.value in ("**추천 A**", "**추천 B**")],
                                 ["**추천 A**", "**추천 B**"])
                choice[0].set_value("A").run()
                self.assertFalse(at.exception)
                rows = experiments.load_feedback()
            self.assertEqual(len(rows), 2)                          # 턴마다 한 건(평가를 안 눌러도 요청 종류와 토큰은 남는다)
            self.assertEqual(sorted(row["action"] for row in rows), ["courses", "gap"])
            chosen = [row for row in rows if row.get("picked")]
            self.assertEqual(len(chosen), 1)
            self.assertIn(chosen[0]["picked"], ("single", "reviewed"))
            self.assertEqual({chosen[0]["left"], chosen[0]["right"]}, {"single", "reviewed"})
            self.assertNotIn("추천해줘", log.read_text(encoding="utf-8"))

    def diagnose(self, at, levels):
        """모달 저장과 같은 상태를 만든다(AppTest 는 모달 안 버튼을 다시 그리지 못한다)."""
        at.session_state["levels"] = levels
        at.session_state["levels_example"] = False
        return at.run()

    def test_only_diagnosed_levels_travel_to_chat(self):
        at = self.open_screen()
        with patch("backend.chat.handle_message", return_value=SimpleNamespace(text="확인", courses=[])) as handle:
            at.chat_input[0].set_value("내 상태를 정리해줘").run()
            self.assertEqual(handle.call_args.kwargs["sidebar_skills"], [])  # 진단 전 예시 값은 넘기지 않는다
            self.diagnose(at, {"C1-4": 3, "C5-2": 2})
            at.chat_input[0].set_value("다시 정리해줘").run()
            # 컨테이너 3단계는 기준(3)을 넘어 Docker·Kubernetes 보유, 스크립트 2단계는 Python 미보유
            self.assertEqual(handle.call_args.kwargs["sidebar_skills"], ["Docker", "Kubernetes"])
        at.radio(key="view").set_value("dashboard").run()
        self.assertEqual(at.session_state["levels"], {"C1-4": 3, "C5-2": 2})
        self.assertEqual(len(at.tabs), 4)
        self.assertFalse(at.exception)

    def test_levels_are_saved_and_reloaded_by_nickname(self):
        from app import levels as levels_screen

        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"SKILLPATH_MOCK_PROFILE_DB": str(Path(folder) / "p.sqlite3")}))
        chosen = {sid: 0 for sid in levels_screen.level_guide()["subskills"]}
        chosen.update({"C1-4": 4, "C2-3": 2})
        class State(dict):
            __getattr__, __setattr__ = dict.get, dict.__setitem__

        with patch.object(levels_screen.st, "session_state", State()):
            levels_screen.save_levels("수준 예시", chosen)
        self.assertEqual({k: v for k, v in chat_profiles.load_profile("수준 예시")["levels"].items() if v},
                         {"C1-4": 4, "C2-3": 2})
        at = self.open_screen("dashboard")
        self.assertTrue(at.session_state["levels_example"])
        at.text_input(key="chat_nickname").set_value("수준 예시").run()
        self.assertFalse(at.session_state["levels_example"])
        self.assertEqual(at.session_state["levels"]["C1-4"], 4)
        at.text_input(key="chat_nickname").set_value("다른 사람").run()
        self.assertTrue(at.session_state["levels_example"])  # 다른 닉네임은 예시 값으로 돌아간다
        self.assertFalse(at.exception)

    def test_nickname_and_reruns_do_not_replay_or_leak_messages(self):
        at = self.open_screen()
        echo = "![text](https://example.com/image)"
        with patch("backend.chat.handle_message", return_value=SimpleNamespace(text=echo, courses=[])) as handle:
            at.chat_input[0].set_value(echo).run()
            at.text_input(key="chat_nickname").set_value("첫째").run()
            self.assertEqual([m.text[0].value for m in at.chat_message], [echo, echo])
            self.assertFalse(any(echo in m.value for m in at.markdown))
            self.diagnose(at, {"C1-4": 3})
            at.chat_input[0].set_value("Docker 경험을 저장해줘").run()
            self.assertEqual(handle.call_args.kwargs["nickname"], "첫째")
            # 공통 기술(AWS·Azure)은 고른 세부 능력 평균(3)이라 기준을 넘는다
            self.assertEqual(handle.call_args.kwargs["sidebar_skills"], ["AWS", "Docker", "Kubernetes", "Azure"])
            first_state = handle.call_args.args[0]
            at.radio(key="view").set_value("dashboard").run()
            at.radio(key="view").set_value("chat").run()
            self.assertEqual(len(at.chat_message), 4)
            self.assertEqual(at.text_input(key="chat_nickname").value, "첫째")
            at.text_input(key="chat_nickname").set_value("둘째").run()
            self.assertFalse(at.chat_message)
            self.assertTrue(at.session_state["levels_example"])
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
