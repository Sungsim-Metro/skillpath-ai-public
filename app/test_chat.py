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
    password = "example profile password"

    def setUp(self):
        self.profile_db = Path(self.enterContext(TemporaryDirectory())) / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1", "USAGE_LOG": "off",
            "SKILLPATH_MOCK_PROFILE_DB": str(self.profile_db),
        }))

    def open_screen(self, view="chat"):
        at = AppTest.from_file(MAIN, default_timeout=60)
        at.query_params["view"] = view
        at.run()
        self.assertFalse(at.exception)
        return at

    def authenticate(self, at, nickname, *, register=False, password=None):
        at.text_input(key="auth_nickname").set_value(nickname)
        at.text_input(key="auth_password").set_value(self.password if password is None else password)
        at.button(key="auth_register" if register else "auth_login").click().run()
        self.assertFalse(at.exception)

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

        self.authenticate(at, "처음 닉네임", register=True)
        at.radio(key="view").set_value("gap").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(at.metric[0].value, "110")
        self.assertEqual(session_id, at.session_state["chat_conversation"].usage.session_id)
        at.button(key="auth_logout").click().run()
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
        at.radio(key="view").set_value("gap").run()
        self.assertEqual(at.session_state["levels"], {"C1-4": 3, "C5-2": 2})
        self.assertEqual(at.title[0].value, "부족 역량")
        self.assertFalse(at.exception)

    def test_levels_require_authentication_before_they_are_reloaded(self):
        session = chat_profiles.create_profile("수준 예시", self.password)
        chat_profiles.update_profile(session, levels={"C1-4": 4, "C2-3": 2})
        at = self.open_screen("gap")
        with patch.object(chat_profiles, "load_profile", wraps=chat_profiles.load_profile) as read:
            at.text_input(key="auth_nickname").set_value("수준 예시").run()
            self.authenticate(at, "수준 예시", password="incorrect example password")
            read.assert_not_called()
            self.assertTrue(at.session_state["levels_example"])
            self.assertEqual(at.text_input(key="auth_password").value, "")
            self.assertTrue(at.error)
            self.authenticate(at, "수준 예시")
        self.assertFalse(at.session_state["levels_example"])
        self.assertEqual(at.session_state["levels"]["C1-4"], 4)
        at.button(key="auth_logout").click().run()
        self.assertTrue(at.session_state["levels_example"])
        self.assertTrue(at.button(key="open_report").disabled)
        self.assertEqual(at.text_input(key="auth_password").value, "")
        self.assertFalse(at.exception)

    def test_logout_and_failed_sign_in_clear_previous_private_state(self):
        at = self.open_screen()
        self.authenticate(at, "첫째", register=True)
        self.diagnose(at, {"C1-4": 3})
        at.chat_input[0].set_value("프로젝트에서 제가 Docker로 API를 배포했어요.").run()
        at.radio(key="view").set_value("gap").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(len(at.chat_message), 2)
        first_state = at.session_state["chat_conversation"]
        self.assertEqual(first_state.session.nickname, "첫째")
        for key in ("lv_page", "extraction", "extracted_text"):
            at.session_state[key] = "private draft"
        at.button(key="auth_logout").click().run()
        with (patch.object(chat_profiles, "load_profile", wraps=chat_profiles.load_profile) as read,
              patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as write):
            self.authenticate(at, "첫째", password="incorrect example password")
            self.assertFalse(at.chat_message)
            self.assertIsNot(at.session_state["chat_conversation"], first_state)
            self.assertTrue(at.session_state["levels_example"])
            self.assertTrue(at.button(key="open_report").disabled)
            for key in ("lv_page", "extraction", "extracted_text", "profile_session"):
                self.assertNotIn(key, at.session_state)
            at.chat_input[0].set_value("내 상태를 정리해줘").run()
            self.assertNotIn("Docker", at.chat_message[-1].text[0].value)
            read.assert_not_called()
            write.assert_not_called()
        self.authenticate(at, "둘째", register=True)
        self.assertEqual(at.session_state["profile_session"].nickname, "둘째")

    def test_legacy_nickname_session_starts_again_as_guest(self):
        at = self.open_screen()
        at.session_state["chat_identity"] = "기존 닉네임"
        at.session_state["chat_history"] = [("private message", "private reply", [])]
        at.session_state["levels"] = {"C1-4": 5}
        at.session_state["levels_example"] = False
        with patch.object(chat_profiles, "load_profile", wraps=chat_profiles.load_profile) as read:
            at.run()
            self.assertFalse(at.chat_message)
            self.assertTrue(at.session_state["levels_example"])
            read.assert_not_called()
        self.assertNotIn("chat_identity", at.session_state)

    def test_legacy_tuple_history_shows_courses_without_feedback_for_guests_and_users(self):
        self.enterContext(patch.dict(os.environ, {"AB_STRUCTURES": "single,reviewed"}))
        legacy_course = {"title": "이전 추천 과정", "match": "certain", "url": "https://example.com/legacy"}
        for signed_in in (False, True):
            with self.subTest(signed_in=signed_in):
                at = self.open_screen()
                if signed_in:
                    self.authenticate(at, "기존 세션 예시", register=True)
                at.session_state["chat_history"] = [("이전 질문", "이전 답변", [legacy_course])]
                at.run()
                self.assertFalse(at.exception)
                self.assertEqual(at.chat_message[0].text[0].value, "이전 질문")
                self.assertEqual(at.chat_message[1].text[0].value, "이전 답변")
                self.assertEqual(at.get("link_button")[0].proto.url, legacy_course["url"])
                self.assertFalse(at.get("feedback"))

    def test_saving_a_guest_experience_after_login(self):
        at = self.open_screen()
        at.chat_input[0].set_value("프로젝트에서 제가 Docker로 API를 배포했어요.").run()
        with patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as save:
            at.chat_input[0].set_value("방금 말한 Docker 배포 경험을 프로필에 저장해줘.").run()
            self.assertIn("로그인", at.chat_message[-1].text[0].value)
            save.assert_not_called()
            self.assertFalse(self.profile_db.exists())
            self.authenticate(at, "상담 예시", register=True)
            self.assertEqual(len(at.chat_message), 4)
            at.chat_input[0].set_value("방금 말한 Docker 배포 경험을 프로필에 저장해줘.").run()
            self.assertIn("저장했습니다", at.chat_message[-1].text[0].value)
            at.run()
            self.assertEqual(save.call_count, 1)
        session = at.session_state["profile_session"]
        profile = chat_profiles.load_profile(session)
        self.assertFalse(profile["skills"])
        self.assertEqual(len(profile["experiences"]), 1)
        self.assertIn("Docker", profile["experiences"][0])
        reopened = self.open_screen()
        self.authenticate(reopened, "상담 예시")
        self.assertFalse(reopened.chat_message)
        with patch.object(api, "get_gap", wraps=api.get_gap) as gap:
            reopened.chat_input[0].set_value("내 경험 기준으로 뭐가 부족해?").run()
            gap.assert_called_once_with("cloud_engineer", ["Docker"])
        self.assertEqual(chat_profiles.load_profile(session), profile)

    def test_chat_reuses_one_unlisted_skill_across_jobs_and_sign_ins(self):
        first = self.open_screen()
        self.authenticate(first, "역량 예시", register=True, password="x")
        first.chat_input[0].set_value("제가 Acme.Flow를 사용했어요. Acme.Flow만 프로필에 저장해줘.").run()
        session = first.session_state["profile_session"]
        self.assertEqual(chat_profiles.load_profile(session)["skills"], ["Acme.Flow"])
        first.button(key="auth_logout").click().run()

        second = self.open_screen()
        self.authenticate(second, "역량 예시", password="x")
        second.selectbox(key="job").set_value("ai_engineer").run()
        second.chat_input[0].set_value("제가 acme.flow를 사용했어요. acme.flow만 프로필에 저장해줘.").run()
        self.assertFalse(second.exception)
        profile = chat_profiles.load_profile(second.session_state["profile_session"])
        self.assertEqual(profile["skills"], ["Acme.Flow"])
        self.assertEqual(profile["levels"], {})

    def test_returned_course_links_have_plain_text_details(self):
        at = self.open_screen()
        course = {"title": "[예시 과정]", "url": "https://example.com/course", "match": "uncertain",
                  "level": "기초", "prerequisites": "없음", "reason": "과정에서 배포 실습을 다룹니다."}
        invalid = {**course, "url": "javascript:alert(1)"}
        plain_text = "![텍스트](https://example.com/image)"
        reply = SimpleNamespace(text=plain_text, courses=[course, invalid])
        with patch("backend.chat.handle_message", return_value=reply):
            at.chat_input[0].set_value(plain_text).run()
            at.chat_input[0].set_value("과정을 다시 보여줘").run()
        self.assertEqual([message.text[0].value for message in at.chat_message[:2]], [plain_text, plain_text])
        self.assertFalse(any(plain_text in block.value for block in at.markdown))
        self.assertFalse(at.exception)
        self.assertEqual([item.proto.url for item in at.get("link_button")], [course["url"], course["url"]])
        details = " ".join(item.value for item in at.chat_message[-1].text)
        for expected in (course["title"], course["reason"], "불확실"):
            self.assertIn(expected, details)
        self.assertNotIn("★", details)  # 평점이 없는 과정은 평점 줄이 없다

    def test_cards_show_rank_and_rating_and_shortcuts_open_the_matching_screen(self):
        at = self.open_screen()
        course = {"title": "예시 과정", "url": "https://example.com/course", "match": "certain", "level": "기초",
                  "prerequisites": "없음", "reason": "배포 실습을 다룹니다.", "gap_skill": "Docker"}
        rated = {**course, "title": "평점 있는 과정", "rating_avg": 4.6, "rating_count": 1280}
        other = {**course, "title": "다른 기술 과정", "gap_skill": "Linux"}
        reply = SimpleNamespace(text="추천입니다.", courses=[rated, course, other], action="courses")
        with patch("backend.chat.handle_message", return_value=reply):
            at.chat_input[0].set_value("과정 추천해줘").run()
        self.assertFalse(at.exception)
        answer = at.chat_message[-1]
        self.assertEqual([t.value for t in answer.text if t.value.startswith("★")], ["★ 4.6 · 평가 1,280개"])
        badges = [m.value for m in answer.markdown if "위]" in m.value]
        self.assertEqual([b.split("]")[0] for b in badges], [":blue-badge[1위", ":blue-badge[2위", ":blue-badge[1위"])
        self.assertEqual([b.label for b in answer.button], ["추천 과정 보기"])
        answer.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "recommend")
        self.assertEqual(at.title[0].value, "학습 추천")

        at = self.open_screen()
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("go_")])  # 경험 입력에는 바로가기가 없다
        at.chat_input[0].set_value("클라우드 엔지니어 기준으로 뭐가 부족해?").run()
        shortcut = [b for b in at.button if b.key and b.key.startswith("go_")]
        self.assertEqual([b.label for b in shortcut], ["부족 역량 자세히 보기"])
        shortcut[0].click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "gap")
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(len(at.chat_message), 4)  # 다녀와도 대화가 남아 있다


if __name__ == "__main__":
    unittest.main()
