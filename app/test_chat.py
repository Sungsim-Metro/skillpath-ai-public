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

from app.testing import set_job

from ai.chat import Turn
from backend import api, chat_profiles
from backend.jobs import JOBS

MAIN = str(Path(__file__).with_name("main.py"))


def tokens(at) -> str:
    """이 대화의 누적 토큰. 화면에는 보여 주지 않아 상담 상태에서 읽는다."""
    return f'{at.session_state["chat_conversation"].usage.total_tokens:,}'


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
        at.session_state["entered"] = True  # 로그인·회원가입 화면을 비회원으로 지난 상태(app/test_entry.py 가 그 화면을 본다)
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
            self.assertEqual(tokens(at), "33")
            at.chat_input[0].set_value("다시 정리해줘").run()
            self.assertIn("해석하지 못했습니다", at.chat_message[-1].text[0].value)
            self.assertEqual(tokens(at), "110")
            usage = at.session_state["chat_conversation"].usage
            self.assertEqual((usage.input_tokens, usage.output_tokens, usage.session_id), (100, 10, session_id))
            self.assertFalse(at.metric)  # 토큰 수는 상담 화면에 보여 주지 않는다
            self.assertFalse(any("세션 ID" in c.value or "토큰" in c.value for c in at.caption))
            at.run()
            other.run()
            self.assertEqual(tokens(at), "110")
            self.assertEqual(tokens(other), "0")
            self.assertEqual(call.call_count, 4)

        self.authenticate(at, "처음 닉네임", register=True)
        at.radio(key="view").set_value("gap").run()
        at.radio(key="view").set_value("chat").run()
        self.assertEqual(tokens(at), "110")
        self.assertEqual(session_id, at.session_state["chat_conversation"].usage.session_id)
        at.button(key="auth_logout").click().run()
        self.assertEqual(tokens(at), "0")
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
        self.assertEqual(tokens(at), "0")
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
                def choices(screen):
                    return [b for b in screen.button if b.key and b.key.startswith("ab_")]

                self.assertFalse(choices(at))
                at.chat_input[0].set_value("그중 먼저 들을 교육과정을 추천해줘.").run()
                self.assertFalse(at.exception)
                self.assertEqual([b.label for b in choices(at)], ["추천 A 고르기", "추천 B 고르기", "둘이 비슷해요"])
                self.assertEqual([m.value for m in at.markdown if m.value in ("**추천 A**", "**추천 B**")],
                                 ["**추천 A**", "**추천 B**"])
                choices(at)[0].click().run()
                self.assertFalse(at.exception)
                self.assertFalse(choices(at))                       # 고르면 선택 버튼이 사라지고
                self.assertTrue(any("이 추천을 골랐습니다" in c.value for c in at.caption))
                self.assertEqual([t.value for t in at.toast], ["의견 주셔서 감사합니다!"])
                rated = at.get("feedback")
                self.assertEqual(len(rated), 2)                     # 답 두 개에 평가 위젯이 하나씩
                at.session_state[rated[0].key] = 1                  # 좋아요를 누른 것과 같은 상태로 콜백을 부른다
                from app import chat as chat_screen
                entry = at.session_state["chat_history"][0]
                with patch.object(chat_screen.st, "session_state", {rated[0].key: 1}), \
                     patch.object(chat_screen.st, "toast") as toast:
                    chat_screen._on_rating(entry)
                toast.assert_called_once_with("의견 주셔서 감사합니다!")
                failed = at.session_state["chat_history"][1]
                with patch.object(chat_screen.st, "session_state", {f"fb_{failed['turn_id']}": 0}),                      patch.object(chat_screen.st, "toast") as toast,                      patch.object(experiments, "save_feedback", side_effect=RuntimeError("down")):
                    chat_screen._on_rating(failed)
                self.assertNotIn("rated", failed)                   # 저장하지 못한 평가는 숨기지 않고 감사 인사도 하지 않는다
                self.assertNotIn("감사", toast.call_args.args[0])
                at.run()
                self.assertEqual(len(at.get("feedback")), 1)        # 평가한 답의 위젯은 숨긴다
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
            self.assertIsNone(handle.call_args.kwargs["sidebar_levels"])
            self.diagnose(at, {"C1-4": 3, "C5-2": 2})
            at.chat_input[0].set_value("다시 정리해줘").run()
            # 컨테이너 3단계는 기준(3)을 넘어 Docker·Kubernetes 보유, 스크립트 2단계는 Python 미보유
            self.assertEqual(handle.call_args.kwargs["sidebar_skills"], ["Docker", "Kubernetes"])
            self.assertEqual(handle.call_args.kwargs["sidebar_levels"], {"C1-4": 3, "C5-2": 2})
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
        self.assertEqual(at.text_input(key="auth_password").value, "")
        self.assertFalse(at.exception)

    def test_chat_question_applies_only_selected_level_and_restores_it_on_login(self):
        at = self.open_screen()
        self.authenticate(at, "문항 응답", register=True)
        session = at.session_state["profile_session"]
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        choice = at.radio(key="chat_level_C1-4")
        self.assertIsNone(choice.value)
        self.assertTrue(any("Dockerfile" in label for label in choice.options))
        self.assertTrue(at.button(key="apply_chat_levels").disabled)
        # 닫아 둔 설문의 다른 임시 응답을 유지하면서 채팅에서 답한 문항을 동기화한다.
        at.session_state["lv_initial_cloud_engineer"] = {"C1-4": None, "C1-5": None}
        at.session_state["lv_C1-5"] = 1
        choice.set_value(2).run()
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {})
        at.button(key="apply_chat_levels").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {"C1-4": 2})
        self.assertEqual(at.session_state["levels"], {"C1-4": 2})
        self.assertEqual(at.session_state["lv_initial_cloud_engineer"]["C1-4"], 2)
        self.assertEqual(at.session_state["lv_C1-5"], 1)
        self.assertFalse([r for r in at.radio if r.key == "chat_level_C1-4"])
        with patch.object(api, "get_gap", wraps=api.get_gap) as gap:
            at.chat_input[0].set_value("내 경험 기준으로 뭐가 부족해?").run()
            gap.assert_called_once_with("cloud_engineer", {"C1-4": 2})
        at.button(key="auth_logout").click().run()
        self.authenticate(at, "문항 응답")
        self.assertEqual(at.session_state["levels"], {"C1-4": 2})

    def test_guest_can_skip_then_explicitly_apply_zero_without_persisting(self):
        at = self.open_screen()
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        at.button(key="skip_chat_levels").click().run()
        self.assertTrue(at.session_state["levels_example"])
        at.chat_input[0].set_value("제가 Kubernetes로 서비스를 배포했어요.").run()
        at.radio(key="chat_level_C1-4").set_value(0).run()
        at.button(key="apply_chat_levels").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["levels"], {"C1-4": 0})
        self.assertFalse(self.profile_db.exists())

    def test_saved_survey_can_be_reopened_skipped_and_revised_in_chat(self):
        session = chat_profiles.create_profile("기존 진단", self.password)
        chat_profiles.update_profile(session, levels={"C1-4": 3, "C5-2": 1})
        at = self.open_screen()
        self.authenticate(at, "기존 진단")
        message = "제가 Docker로 API를 배포했어요."
        with patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as write:
            at.chat_input[0].set_value(message).run()
            prompt = next(r for r in at.radio if "컨테이너" in r.label)
            self.assertEqual(prompt.value, 3)
            self.assertTrue(at.button(key="apply_chat_levels").disabled)
            prompt.set_value(1).run()
            at.button(key="skip_chat_levels").click().run()
            self.assertFalse(any("컨테이너" in r.label for r in at.radio))
            at.chat_input[0].set_value(message).run()
            prompt = next(r for r in at.radio if "컨테이너" in r.label)
            self.assertEqual(prompt.value, 3)
            prompt.set_value(0).run()
            write.assert_not_called()
            at.button(key="apply_chat_levels").click().run()
            at.chat_input[0].set_value("내 경험을 요약해줘.").run()
            self.assertFalse(any("컨테이너" in r.label for r in at.radio))
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.kwargs["levels"], {"C1-4": 0})
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {"C1-4": 0, "C5-2": 1})
        at.chat_input[0].set_value(message).run()
        self.assertEqual(next(r.value for r in at.radio if "컨테이너" in r.label), 0)

    def test_existing_direct_rating_can_be_updated_independently_of_survey(self):
        session = chat_profiles.create_profile("직접 입력 진단", self.password)
        chat_profiles.update_profile(session, levels={"C1-4": 2, "custom:Docker": 0})
        at = self.open_screen()
        self.authenticate(at, "직접 입력 진단")
        at.chat_input[0].set_value("제가 도커를 사용했어요.").run()
        self.assertEqual(next(r.value for r in at.radio if "컨테이너" in r.label), 2)
        direct = next(r for r in at.radio if "직접" in r.label and "Docker" in r.label)
        self.assertEqual(direct.value, 0)
        direct.set_value(4).run()
        with patch.object(chat_profiles, "update_profile", wraps=chat_profiles.update_profile) as write:
            at.button(key="apply_chat_levels").click().run()
            self.assertEqual(write.call_args.kwargs["levels"], {"custom:Docker": 4})
        self.assertFalse(at.exception)
        saved = chat_profiles.load_profile(session)["levels"]
        self.assertEqual(saved, {"C1-4": 2, "custom:Docker": 4})
        gap = {r["skill"]: r["user_level"] for r in api.get_gap("cloud_engineer", saved)}
        self.assertEqual((gap["Docker"], gap["Kubernetes"]), (4, 2))

    def test_leaving_the_assessment_role_discards_an_unapplied_edit(self):
        at = self.open_screen()
        self.diagnose(at, {"C1-4": 3})
        message = "제가 Docker로 API를 배포했어요."
        at.chat_input[0].set_value(message).run()
        next(r for r in at.radio if "컨테이너" in r.label).set_value(1).run()
        set_job(at, "ai_engineer")
        set_job(at, "cloud_engineer")
        at.chat_input[0].set_value(message).run()
        self.assertEqual(next(r.value for r in at.radio if "컨테이너" in r.label), 3)
        self.assertTrue(at.button(key="apply_chat_levels").disabled)

    def test_chat_assessment_retries_failed_profile_save_without_resending_chat(self):
        at = self.open_screen()
        self.authenticate(at, "재시도", register=True)
        session = at.session_state["profile_session"]
        at.chat_input[0].set_value("제가 Docker로 API를 배포했어요.").run()
        at.radio(key="chat_level_C1-4").set_value(4).run()
        with patch.object(chat_profiles, "update_profile", side_effect=OSError):
            at.button(key="apply_chat_levels").click().run()
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {})
        self.assertEqual(at.session_state["levels"], {"C1-4": 4})
        at.button(key="retry_levels").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {"C1-4": 4})
        self.assertEqual(len(at.session_state["chat_history"]), 1)

    def test_only_current_role_questions_are_shown_and_partial_answers_are_kept(self):
        at = self.open_screen()
        at.chat_input[0].set_value("제가 Docker와 Kubernetes를 사용했어요.").run()
        question = at.radio(key="chat_level_C1-4")
        self.assertIn("Docker", question.label)
        self.assertIn("Kubernetes", question.label)
        question.set_value(4).run()
        set_job(at, "ai_engineer")
        self.assertFalse([r for r in at.radio if r.key.startswith("chat_level_")])
        at.chat_input[0].set_value("제가 Python을 사용했어요.").run()
        at.radio(key="chat_level_A2-2").set_value(1).run()
        at.button(key="apply_chat_levels").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["levels"], {"A2-2": 1})
        self.assertIsNone(at.radio(key="chat_level_A2-1").value)

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
            gap.assert_called_once_with("cloud_engineer", {})
            self.assertIn("Docker", reopened.chat_message[-1].text[0].value)
        self.assertEqual(chat_profiles.load_profile(session), profile)

    def test_chat_reuses_one_unlisted_skill_across_jobs_and_sign_ins(self):
        first = self.open_screen()
        self.authenticate(first, "역량 예시", register=True, password="x")
        first.chat_input[0].set_value("제가 Acme.Flow를 사용했어요. Acme.Flow만 프로필에 저장해줘.").run()
        self.assertFalse([r for r in first.radio if r.key.startswith("chat_level_")])
        session = first.session_state["profile_session"]
        self.assertEqual(chat_profiles.load_profile(session)["skills"], ["Acme.Flow"])
        first.button(key="auth_logout").click().run()

        second = self.open_screen()
        self.authenticate(second, "역량 예시", password="x")
        set_job(second, "ai_engineer")
        second.chat_input[0].set_value("제가 acme.flow를 사용했어요. acme.flow만 프로필에 저장해줘.").run()
        self.assertFalse(second.exception)
        profile = chat_profiles.load_profile(second.session_state["profile_session"])
        self.assertEqual(profile["skills"], ["Acme.Flow"])
        self.assertEqual(profile["levels"], {})

    def test_suggested_questions_new_chat_and_the_way_back_from_a_shortcut(self):
        at = self.open_screen()
        suggestions = [b for b in at.button if b.key and b.key.startswith("suggest_")]
        self.assertEqual(len(suggestions), 4)
        self.assertTrue(any("목표 직무 클라우드 엔지니어" in c.value and "수준 진단 전" in c.value for c in at.caption))
        next(b for b in suggestions if "부족" in b.label).click().run()  # 추천 질문을 누르면 그 글이 메시지로 들어간다
        self.assertFalse(at.exception)
        self.assertEqual(len(at.chat_message), 2)
        self.assertEqual(at.chat_message[0].text[0].value, "지금 뭐가 부족해?")
        self.assertNotIn("chat_suggested", at.session_state)
        at.run()
        self.assertEqual(len(at.chat_message), 2)  # 다시 그려도 같은 질문을 또 보내지 않는다

        self.assertFalse([b for b in at.button if b.key == "back_to_chat"])
        next(b for b in at.button if b.key and b.key.startswith("go_")).click().run()
        self.assertEqual(at.session_state["view"], "gap")
        at.button(key="back_to_chat").click().run()  # 바로가기로 넘어간 화면에서 상담으로 돌아간다
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["view"], "chat")
        self.assertEqual(len(at.chat_message), 2)
        at.radio(key="view").set_value("gap").run()
        self.assertFalse([b for b in at.button if b.key == "back_to_chat"])  # 메뉴로 직접 가면 돌아가기 버튼이 없다
        at.radio(key="view").set_value("chat").run()
        next(b for b in at.button if b.key and b.key.startswith("go_")).click().run()
        at.radio(key="view").set_value("demand").run()  # 바로가기로 간 뒤 메뉴로 다른 화면에 가면 버튼이 남지 않는다
        self.assertFalse([b for b in at.button if b.key == "back_to_chat"])
        at.radio(key="view").set_value("gap").run()
        self.assertFalse([b for b in at.button if b.key == "back_to_chat"])
        at.radio(key="view").set_value("chat").run()

        self.diagnose(at, {"C1-4": 3})
        at.button(key="chat_reset").click().run()  # 새 상담: 대화만 비우고 진단은 그대로 둔다
        self.assertFalse(at.exception)
        self.assertFalse(at.chat_message)
        self.assertEqual(at.session_state["levels"], {"C1-4": 3})
        self.assertTrue(any("진단으로 확인한 보유 기술" in c.value for c in at.caption))

    def test_level_questions_survive_the_turn_that_sets_the_job_by_conversation(self):
        at = self.open_screen()
        self.assertEqual(at.session_state["job"], "cloud_engineer")
        at.chat_input[0].set_value("AI 엔지니어 준비 중이고 제가 RAG를 사용했어요.").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["job"], "ai_engineer")  # 대화로 직무가 바뀌고
        pending = at.session_state["chat_assessment"]            # 그 턴에 말한 기술의 문항도 남는다
        self.assertEqual((pending[0], pending[1]), ("ai_engineer", ["RAG"]))

    def test_survey_button_appears_only_with_an_https_address(self):
        self.assertFalse(self.open_screen().sidebar.get("link_button"))
        with patch.dict(os.environ, {"SURVEY_URL": "javascript:alert(1)"}):
            self.assertFalse(self.open_screen().sidebar.get("link_button"))
        with patch.dict(os.environ, {"SURVEY_URL": "https://example.com/form"}):
            links = self.open_screen().sidebar.get("link_button")
        self.assertEqual([item.proto.url for item in links], ["https://example.com/form"])

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
        answer = at.chat_message[-1]
        details = " ".join(item.value for item in [*answer.text, *answer.markdown, *answer.caption]).replace("\\", "")
        for expected in (course["title"], course["reason"], "유사 과정"):
            self.assertIn(expected, details)
        self.assertNotIn("★", details)  # 평점이 없는 과정은 평점 줄이 없다
        self.assertFalse([b for b in answer.get("link_button") if b.proto.label.startswith("★")])

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
        self.assertEqual([b.proto.label for b in answer.get("link_button") if b.proto.label.startswith("★")],
                         ["★ 4.6 · 평가 1,280개"])
        self.assertEqual([tab.label for tab in answer.tabs], ["Docker", "Linux"])  # 기술이 여럿이면 탭으로 나눈다
        badges = [m.value for m in answer.markdown if "위]" in m.value]
        self.assertEqual([b.split("]")[0] for b in badges], [":blue-badge[1위", ":blue-badge[2위", ":blue-badge[1위"])
        self.assertEqual([b.label for b in answer.button], ["담기", "담기", "담기", "추천 과정 보기"])
        self.assertNotIn("saved_courses", at.session_state)
        answer.button[0].click().run()  # 과정을 담는다(모아 보는 화면은 app/test_saved.py 가 본다)
        self.assertFalse(at.exception)
        self.assertEqual([c["title"] for c in at.session_state["saved_courses"].values()], ["평점 있는 과정"])
        answer = at.chat_message[-1]
        self.assertEqual([b.label for b in answer.button][:3], ["담아 둠"] * 3)  # 테스트의 세 과정은 주소가 같아 함께 바뀐다
        answer.button[0].click().run()  # 다시 누르면 뺀다
        self.assertEqual(at.session_state["saved_courses"], {})
        answer = at.chat_message[-1]
        next(b for b in answer.button if b.label == "추천 과정 보기").click().run()
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
