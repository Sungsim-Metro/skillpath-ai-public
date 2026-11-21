"""상담 한 턴이 수행하는 작업의 범위와 프로필 변경을 확인한다."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ai.chat import Turn
from backend import api, chat, chat_profiles
from backend.api import get_gap as dashboard_gap


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = cls.enterClassContext(TemporaryDirectory())
        cls.enterClassContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "SKILLPATH_MOCK_PROFILE_DB": str(Path(folder) / "profiles.db"),
        }))
        cls.first = chat_profiles.create_profile("first example", "first example password")
        cls.second = chat_profiles.create_profile("second example", "second example password")

    def setUp(self):
        self.state = chat.Conversation()
        self.guard = self.start("ai.chat.guard_input", return_value=True)
        self.intake = self.start("ai.chat.interpret")
        self.gap = self.start("backend.api.get_gap")
        self.recs = self.start("backend.api.get_recommendations")
        self.read = self.start("backend.chat_profiles.load_profile", return_value={
            "job": None, "skills": ["Linux"], "experiences": [],
        })
        self.write = self.start("backend.chat_profiles.update_profile")

    def start(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def send(self, turn, text="현재 상태", session=None, skills=(), levels=None):
        self.intake.return_value = turn
        return chat.handle_message(self.state, text, job="cloud_engineer",
                                   sidebar_skills=list(skills), session=session, sidebar_levels=levels)

    def test_blocked_input_never_reaches_context_or_tools(self):
        self.guard.return_value = False
        reply = self.send(Turn(), session=self.first)
        self.assertTrue(reply.text)
        for fn in (self.intake, self.read, self.write, self.gap, self.recs):
            fn.assert_not_called()

    def test_draft_and_summary_merge_only_confirmed_facts(self):
        self.send(Turn(add_skills=["도커"], add_experiences=["API 직접 배포"]), session=self.first)
        self.send(Turn(status="incomplete"), session=self.first)
        self.send(Turn(remove_skills=["Linux"]), session=self.first)
        reply = self.send(Turn(action="summary"), session=self.first, skills=["Git", "Linux"])
        self.assertIn("Docker", reply.text)
        self.assertIn("Git", reply.text)
        self.assertIn("API 직접 배포", reply.text)
        self.assertNotIn("Linux", reply.text)
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.write.assert_not_called()

    def test_skill_note_does_not_turn_an_echoed_role_into_a_job_choice(self):
        self.intake.return_value = Turn(job="ai_engineer", add_skills=["LangChain"])
        reply = chat.handle_message(self.state, "LangChain은 다뤄봤는데", job="ai_engineer", sidebar_skills=[])
        self.assertIn("LangChain", reply.text)
        self.assertNotIn("목표 직무", reply.text)
        self.assertIsNone(self.state.job)

    def test_offline_acknowledgement_names_the_experience_without_a_generic_receipt(self):
        reply = self.send(Turn(add_skills=["LangChain", "RAG"]), "랭체인이랑 RAG는 써봤어")
        self.assertIn("LangChain", reply.text)
        self.assertIn("RAG", reply.text)
        self.assertNotIn("이번 상담에 반영했습니다", reply.text)
        self.assertEqual(reply.assessment_skills, ["LangChain", "RAG"])

    def test_general_learning_question_can_be_answered_without_analysis_or_profile_actions(self):
        answer = "검색은 질문에 필요한 자료를 찾고, 생성은 그 자료를 바탕으로 답을 만드는 역할이에요."
        with patch("ai.chat_reply.write_reply", return_value=answer) as writer:
            reply = self.send(Turn(), "RAG에서 검색과 생성을 왜 나눠?")
        self.assertEqual(reply.text, answer)
        self.assertEqual(self.state.history[-1]["content"], answer)
        self.assertEqual(writer.call_args.kwargs["action"], "note")
        self.assertEqual(self.state.skills, {})
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.write.assert_not_called()

    def test_profile_save_result_is_never_rewritten(self):
        with patch("ai.chat_reply.write_reply") as writer:
            result = self.send(Turn(add_skills=["Docker"], save=True, save_skills=["Docker"]),
                               "Docker 썼어. 프로필에도 저장해줘")
        self.assertIn("로그인", result.text)
        writer.assert_not_called()

    def test_blocked_generated_answer_does_not_reach_history_or_the_next_turn_offer(self):
        from ai.chat_reply import ReplyBlocked

        with patch("ai.chat_reply.write_reply", side_effect=ReplyBlocked("답변을 표시할 수 없습니다.")):
            result = self.send(Turn(), "RAG에서 검색은 왜 필요해?")
        self.assertEqual((result.action, result.courses), ("blocked", []))
        self.assertEqual(self.state.history, [])
        self.assertFalse(self.state.offered_gap)

    def test_unknown_skill_keeps_its_spelling_across_jobs_and_can_be_corrected(self):
        self.read.return_value = {"job": None, "skills": ["OpenTofu"], "experiences": ["배포 API", "배포 api"]}
        self.write.return_value = self.read.return_value
        self.send(Turn(job="cloud_engineer", add_skills=["opentofu"], experience_skills=["OPENTOFU"]),
                  session=self.first, skills=["OPENtofu"])
        self.gap.return_value = [{"skill": "Docker", "owned": True}]
        self.send(Turn(action="gap", job="ai_engineer", experience_skills=["OPENTOFU"],
                       save=True, save_skills=["openTOFU"]), session=self.first)
        self.gap.assert_called_once_with("ai_engineer", {})
        self.write.assert_called_once_with(self.first, add_skills=["OpenTofu"])
        self.send(Turn(action="summary", remove_skills=["OPENTOFU"], remove_experiences=["배포 api"],
                       save=True, save_skills=["opENTOfu"], save_experiences=["배포 api"]),
                  session=self.first, skills=["openTofu"])
        self.write.assert_called_with(self.first, remove_skills=["OpenTofu"], remove_experiences=["배포 api"])
        summary = self.send(Turn(action="summary", experience_skills=["opentofu"]), session=self.first)
        self.assertNotIn("opentofu", summary.text.casefold())
        self.assertIn("배포 API", summary.text)
        self.assertNotIn("배포 api", summary.text)

    def test_saving_a_correction_uses_the_original_profile_alias(self):
        self.read.return_value = {"job": None, "skills": ["도커"], "experiences": []}
        self.write.return_value = self.read.return_value
        self.send(Turn(save=True, save_skills=["docker"]), session=self.first)
        self.write.assert_called_with(self.first, add_skills=["도커"])
        self.send(Turn(remove_skills=["Docker"], save=True, save_skills=["도커"]), session=self.first)
        self.write.assert_called_with(self.first, remove_skills=["도커"])

    def test_guest_cannot_reuse_profile_data_or_save_after_switch(self):
        self.send(Turn(action="summary"), session=self.first)
        self.read.assert_called_once_with(self.first)
        context = self.intake.call_args.args[1]
        self.assertNotIn(self.first.nickname, repr(context))
        self.assertNotIn("session", context)
        self.assertNotIn("nickname", context)
        reply = self.send(Turn(action="summary", save=True, save_job=True))
        self.assertNotIn("Linux", reply.text)
        self.assertIn("로그인", reply.text)
        self.write.assert_not_called()
        self.read.assert_called_once()

    def test_legacy_guest_conversation_remains_usable_after_upgrade(self):
        self.state.skills["Docker"] = True
        fields = vars(self.state).copy()
        del fields["session"]
        self.state = SimpleNamespace(nickname="", **fields)
        reply = self.send(Turn(action="summary"))
        self.assertIn("Docker", reply.text)
        self.read.assert_not_called()

    def test_legacy_named_conversation_requires_a_fresh_login(self):
        self.state.skills["Docker"] = True
        fields = vars(self.state).copy()
        del fields["session"]
        self.state = SimpleNamespace(nickname="legacy", **fields)
        reply = self.send(Turn(action="summary"))
        self.assertNotIn("Docker", reply.text)
        self.read.assert_not_called()

    def test_guard_intervention_preserves_pending_question(self):
        first = self.send(Turn(action="summary"), "내 상태 좀 봐줘.")
        self.assertIn("비교할까요", first.text)
        self.guard.return_value = False
        self.send(Turn(), "차단되는 입력")
        self.assertTrue(self.state.offered_gap)
        self.guard.return_value = True
        self.gap.return_value = [{"skill": "Docker", "owned": True}]
        self.send(Turn(), "네")
        self.gap.assert_called_once()
        self.recs.assert_not_called()

    def test_saved_experience_informs_comparison_without_promoting_it_in_profile(self):
        self.read.return_value = {"job": None, "skills": [], "experiences": ["제가 Docker로 API를 배포했어요"]}
        self.gap.return_value = [{"skill": "Docker", "owned": True}]
        self.send(Turn(action="gap", experience_skills=["Docker"]), session=self.first)
        self.gap.assert_called_with("cloud_engineer", {})
        self.send(Turn(action="gap", remove_skills=["Docker"], experience_skills=["Docker"]), session=self.first)
        self.gap.assert_called_with("cloud_engineer", {})
        self.write.assert_not_called()

    def test_mentioned_but_unrated_skill_is_not_a_deficit_or_a_recommendation(self):
        docker = {"skill": "Docker", "owned": False, "postings": 3, "of": 4, "required_ratio": 0.5}
        self.gap.return_value = [docker, {**docker, "skill": "Git"}]
        self.recs.return_value = []
        reply = self.send(Turn(action="courses", experience_skills=["Docker"]))
        self.assertIn("수준 미입력", reply.text)
        self.assertIn("Docker", reply.text)
        self.assertNotIn("Docker · 공고", reply.text)
        self.assertEqual(self.recs.call_args.args, ("cloud_engineer", ["Git"]))
        self.assertTrue(self.recs.call_args.kwargs["review"])
        self.gap.return_value = [docker]
        self.recs.reset_mock()
        reply = self.send(Turn(action="courses", add_skills=["Docker"]))
        self.assertIn("수준 미입력", reply.text)
        self.assertNotIn("모두 포함", reply.text)
        self.recs.assert_not_called()

    def test_profile_and_sidebar_scores_match_dashboard_even_with_a_saved_skill(self):
        saved = {"custom:Docker": 4}
        self.read.return_value = {"skills": ["Docker"], "levels": saved}
        self.gap.side_effect = lambda job, data: [r for r in dashboard_gap(job, data) if r["skill"] == "Docker"]
        self.recs.side_effect = lambda job, skills, **kw: [
            {"gap_skill": skill, "demand": kw["review_evidence"][skill], "courses": []} for skill in skills
        ]
        for ui, missing in [(None, False), ({}, None), ({"custom:Docker": 0}, True), ({"custom:Docker": 2}, True),
                            ({"custom:Docker": 4}, False), ({"C1-4": 4}, False),
                            ({"C1-4": 4, "custom:Docker": 2}, True)]:
            with self.subTest(sidebar=ui):
                self.recs.reset_mock()
                reply = self.send(Turn(action="courses"), session=self.first, levels=ui)
                self.gap.assert_called_with("cloud_engineer", saved if ui is None else ui)
                self.assertEqual("수준 미입력" in reply.text, missing is None)
                self.assertEqual("Docker에 연결할 교육과정을 찾지 못했습니다." in reply.text, missing is True)
                self.assertEqual(self.recs.called, missing is True)

    def test_denied_skill_with_a_positive_score_is_deferred_only_in_this_chat(self):
        saved = {"custom:Docker": 4}
        self.read.return_value = {"skills": ["Docker"], "levels": saved}
        self.gap.side_effect = lambda job, data: [r for r in dashboard_gap(job, data) if r["skill"] == "Docker"]
        reply = self.send(Turn(action="courses", remove_skills=["Docker"]), session=self.first)
        self.assertIn("비교 보류", reply.text)
        self.assertNotIn("수준 미입력", reply.text)
        self.assertNotIn("모두 포함", reply.text)
        self.recs.assert_not_called()
        self.write.assert_not_called()
        self.assertEqual(self.state.profile["levels"], saved)
        reply = self.send(Turn(action="gap"), session=self.first, levels={"custom:Docker": 0})
        self.assertIn("Docker · 공고", reply.text)
        self.assertNotIn("비교 보류", reply.text)

    def test_assessment_candidates_only_include_a_positive_statement_with_an_active_role(self):
        reply = self.send(Turn(add_skills=["도커", "Git"], remove_skills=["Git"], experience_skills=["Python"]))
        self.assertEqual((reply.assessment_job, reply.assessment_skills), ("cloud_engineer", ["Docker"]))
        self.assertEqual(self.send(Turn(action="summary", experience_skills=["Python"])).assessment_skills, [])
        self.send(Turn(status="unsupported"))
        reply = self.send(Turn(add_skills=["OpenTofu"]))
        self.assertEqual((reply.assessment_job, reply.assessment_skills), ("", []))
        reply = self.send(Turn(job="ai_engineer", add_skills=["OpenTofu"]))
        self.assertEqual((reply.assessment_job, reply.assessment_skills), ("ai_engineer", ["OpenTofu"]))

    def test_gap_then_explicit_course_request(self):
        row = {"skill": "Docker", "postings": 20, "of": 40, "owned": False,
               "required_ratio": 0.45}
        self.gap.return_value = [row]
        self.recs.return_value = [{
            "gap_skill": "Docker", "demand": {"postings": 20, "of": 40, "required_ratio": 0.45},
            "courses": [{"title": "컨테이너 입문", "url": "https://example.com/course",
                         "prerequisites": "없음", "reason": "컨테이너 배포 연습",
                         "level": "기초", "match": "uncertain"}],
        }]
        reply = self.send(Turn(action="gap"))
        self.assertIn("20", reply.text)
        self.recs.assert_not_called()
        reply = self.send(Turn(action="courses"))
        self.assertEqual(reply.courses[0]["match"], "uncertain")
        self.assertEqual(reply.courses[0]["gap_skill"], "Docker")
        self.assertEqual(self.recs.call_args.kwargs["review_evidence"], {
            "Docker": {"postings": 20, "of": 40, "required_ratio": 0.45},
        })
        self.assertTrue(self.recs.call_args.kwargs["review"])

    def test_partial_recommendation_names_the_failed_skill_after_the_outcome(self):
        demand = {"postings": 20, "of": 40, "required_ratio": 0.45}
        self.gap.return_value = [
            {"skill": name, "owned": False, "user_level": 2, "required_level": 3, **demand}
            for name in ("AWS", "FastAPI")
        ]
        self.recs.return_value = [{
            "gap_skill": "AWS", "demand": demand, "courses": [{
                "title": "AWS 입문", "url": "https://example.com/aws", "prerequisites": "없음",
                "reason": "클라우드 기초 학습", "level": "기초", "match": "uncertain",
            }],
        }]
        reply = self.send(Turn(action="courses"), "교육과정 추천해줘", levels={"custom:AWS": 2})
        self.assertEqual([c["title"] for c in reply.courses], ["AWS 입문"])
        self.assertIn("추천", reply.text.splitlines()[0])
        self.assertIn("확인하지 못한 기술 · FastAPI", reply.text)
        self.assertTrue(reply.failed)
        self.assertEqual(self.recs.call_args.kwargs["levels"], {"AWS": 2})

    def test_verification_and_data_failures_are_distinct_from_empty(self):
        self.gap.return_value = [{"skill": "Git", "owned": False, "postings": 2, "of": 3,
                                  "required_ratio": 1.0}]
        self.recs.return_value = [{"gap_skill": "Git", "demand": {"postings": 2, "of": 3,
                                  "required_ratio": 1.0}, "courses": []}]
        empty = self.send(Turn(action="courses"))
        self.assertFalse(empty.courses)
        self.assertIn("찾지 못", empty.text)
        self.recs.return_value[0]["demand"]["postings"] = 50
        invalid = self.send(Turn(action="courses"))
        self.assertIn("검증", invalid.text)
        self.assertIn("Git", invalid.text)
        self.assertFalse(invalid.courses)
        self.gap.side_effect = LookupError("테스트 내부 오류")
        with patch("ai.chat_reply.write_reply") as writer:
            unavailable = self.send(Turn(action="courses"))
        writer.assert_not_called()
        self.assertTrue(unavailable.failed)
        self.assertIn("수요", unavailable.text)
        self.assertNotIn("내부 오류", unavailable.text)
        self.assertEqual(self.recs.call_count, 2)

    def test_save_is_an_independent_scoped_write(self):
        self.send(Turn(add_skills=["Docker"], add_experiences=["Docker로 API 배포"]), session=self.first)
        self.write.assert_not_called()
        self.write.return_value = {"job": None, "skills": ["Linux"], "experiences": ["Docker로 API 배포"]}
        self.gap.return_value = [{"skill": "Linux", "owned": True, "postings": 2, "of": 2}]
        reply = self.send(Turn(action="gap", save=True, save_experiences=["Docker로 API 배포"]),
                          "Docker 배포 경험을 저장하고 부족한 역량도 봐줘", session=self.first)
        self.assertIn("저장", reply.text)
        self.gap.assert_called_once()
        self.recs.assert_not_called()
        args, changes = self.write.call_args
        self.assertEqual(args, (self.first,))
        self.assertEqual(list(changes["add_experiences"]), ["Docker로 API 배포"])
        self.assertFalse(changes.get("add_skills"))
        self.assertFalse(changes.get("job"))

    def test_no_save_of_unknown_fact_or_blocked_job_and_switch_identity(self):
        self.send(Turn(add_skills=["Docker"]), session=self.first)
        self.send(Turn(save=True, save_skills=["Kubernetes"]), session=self.first)
        self.send(Turn(status="unsupported", action="courses", save=True,
                       save_skills=["Docker"]), session=self.first)
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.write.assert_not_called()
        reply = self.send(Turn(action="summary"), session=self.second)
        self.assertNotIn("Docker", reply.text)

    def test_cannot_infer_cloud_after_an_unsupported_target(self):
        self.send(Turn(status="unsupported", action="courses"), "백엔드 직무에 맞는 과정은?")
        self.send(Turn(action="courses"), "그래서 어떤 교육과정이 있어?")
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.gap.return_value = [{"skill": "Docker", "owned": True, "postings": 2, "of": 3}]
        self.send(Turn(job="cloud_engineer", action="courses"), "그럼 클라우드 기준으로 추천해줘")
        self.gap.assert_called_once()
        self.recs.assert_not_called()


class SkillRequirementsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, BEDROCK_MOCK="1", AB_STRUCTURES="", EXPERIMENT_MODELS=""))

    def test_framework_question_uses_job_demand_without_changing_profile_or_running_a_gap(self):
        message = "그런데 TensorFlow도 따로 경험이 많아야하나? 다른 AI 프레임워크로 충분하지는 않아?"
        for architecture in ("screen", "supervised"):
            with self.subTest(architecture=architecture), patch("backend.api.get_gap") as gap:
                state = chat.Conversation()
                reply = chat.handle_message(state, message, job="ai_engineer", sidebar_skills=[], architecture=architecture)
                self.assertEqual(reply.action, "requirements")
                self.assertIn("TensorFlow", reply.text)
                self.assertIn("필수 7건", reply.text)
                self.assertIn("우대 8건", reply.text)
                self.assertIn("중 하나", reply.text)
                self.assertEqual((state.skills, reply.assessment_skills, reply.courses), ({}, [], []))
                gap.assert_not_called()
                if architecture == "supervised":
                    self.assertIn("result_approved", reply.supervisor_decisions)

    def test_compared_skills_keep_separate_counts_even_below_the_personal_gap_cutoff(self):
        snapshot = {"total_postings": 100, "skills": [
            {"skill": "PyTorch", "postings": 31, "required": 21, "preferred": 10},
            {"skill": "TensorFlow", "postings": 3, "required": 1, "preferred": 2},
        ]}
        with patch("backend.api.get_demand", return_value=snapshot):
            reply = chat.handle_message(chat.Conversation(), "파이토치랑 텐서플로우 중 하나만 할 줄 알아도 돼?",
                                        job="ai_engineer", sidebar_skills=[])
        self.assertIn("TensorFlow · 3건", reply.text)
        self.assertIn("PyTorch · 31건", reply.text)
        self.assertNotIn("부족", reply.text)

    def test_requirement_question_does_not_need_a_question_mark(self):
        state = chat.Conversation()
        reply = chat.handle_message(state, "텐서플로우가 꼭 필요한가요", job="ai_engineer", sidebar_skills=[])
        self.assertEqual(reply.action, "requirements")
        next_reply = chat.handle_message(state, "부족한 기술 중 하나만 알려줘", job="ai_engineer", sidebar_skills=[])
        self.assertEqual(next_reply.action, "gap")

    def test_mixed_experience_and_question_compare_only_the_requested_skill(self):
        state = chat.Conversation()
        reply = chat.handle_message(state, "랭체인이랑 RAG는 써봤는데, TensorFlow는 얼마나 필요해?",
                                    job="ai_engineer", sidebar_skills=[])
        self.assertEqual(state.skills, {"LangChain": True, "RAG": True})
        self.assertEqual(reply.action, "requirements")
        self.assertIn("TensorFlow · 15건", reply.text)
        self.assertNotIn("RAG ·", reply.text)

    def test_experience_before_a_comparison_is_not_a_third_comparison_target(self):
        message = "저는 RAG를 써봤어요, 그런데 TensorFlow하고 PyTorch 중 하나만 알아도 충분한가요?"
        for architecture in ("screen", "supervised"):
            with self.subTest(architecture=architecture):
                state = chat.Conversation()
                reply = chat.handle_message(state, message, job="ai_engineer", sidebar_skills=[],
                                            architecture=architecture)
                self.assertEqual(state.skills, {"RAG": True})
                self.assertEqual(reply.action, "requirements")
                self.assertIn("TensorFlow · 15건", reply.text)
                self.assertIn("PyTorch · 30건", reply.text)
                self.assertNotIn("RAG ·", reply.text)


class CourseDifficultyTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, BEDROCK_MOCK="1", AB_STRUCTURES="", USAGE_LOG="off"))

    def send(self, state, text, architecture="screen", job="cloud_engineer"):
        return chat.handle_message(state, text, job=job, sidebar_skills=[], architecture=architecture)

    def test_slash_separated_advanced_topics_have_independent_courses_and_demand(self):
        for mode in ("screen", "supervised"):
            with self.subTest(mode=mode):
                state = chat.Conversation()
                reply = self.send(state, "AI/ LLM 심화 교육과정을 추천받고 싶어", mode, "ai_engineer")
                for topic in ("AI", "LLM"):
                    cards = [card for card in reply.courses if card["gap_skill"] == topic]
                    self.assertEqual([card["level"] for card in cards], ["고급"] * 3)
                    line = next(line for line in reply.text.splitlines() if line.startswith(topic + " · "))
                    self.assertIn("고급 교육과정 3개", line)
                self.assertNotIn("AI/", reply.text)
                self.assertEqual(list(state.last_course_levels), ["AI", "LLM"])

    def test_relative_request_excludes_named_prior_topics(self):
        for mode in ("screen", "supervised"):
            with self.subTest(mode=mode):
                state = chat.Conversation()
                first = self.send(state, "LLM과 RAG 고급 과정을 추천해줘", mode, "ai_engineer")
                self.assertEqual({c["gap_skill"] for c in first.courses}, {"LLM", "RAG"})
                easier = self.send(state, "LLM은 빼고 더 쉬운 걸로 추천해줘", mode, "ai_engineer")
                self.assertEqual({c["gap_skill"] for c in easier.courses}, {"RAG"})
                self.assertNotIn("고급", {c["level"] for c in easier.courses})
                self.assertEqual(list(state.last_course_levels), ["RAG"])
                skipped = self.send(state, "RAG도 제외하고 더 쉬운 걸로 추천해줘", mode, "ai_engineer")
                self.assertFalse(skipped.courses)
                self.assertEqual(skipped.action, "clarify")

    def test_relative_request_uses_displayed_skill_and_does_not_change_user_level(self):
        for mode in ("screen", "supervised"):
            with self.subTest(mode=mode):
                state = chat.Conversation()
                first = self.send(state, "EC2 기초 교육과정을 추천해줘", mode)
                self.assertEqual({(c["gap_skill"], c["level"]) for c in first.courses}, {("EC2", "기초")})
                harder = self.send(state, "난이도 높은 걸로 알려줘", mode)
                self.assertTrue(harder.courses, harder.text)
                self.assertEqual({c["gap_skill"] for c in harder.courses}, {"EC2"})
                self.assertLessEqual({c["level"] for c in harder.courses}, {"중급", "고급"})
                basic = self.send(state, "다시 기초로 추천해줘", mode)
                self.assertEqual({(c["gap_skill"], c["level"]) for c in basic.courses}, {("EC2", "기초")})
                self.assertEqual(state.skills, {})
                self.assertFalse(state.profile)

    def test_advanced_results_and_upper_bound_do_not_fall_back_to_basic(self):
        for mode in ("screen", "supervised"):
            with self.subTest(mode=mode):
                state = chat.Conversation()
                advanced = self.send(state, "AWS 고급 과정만 추천해줘", mode)
                self.assertEqual({(c["gap_skill"], c["level"]) for c in advanced.courses}, {("AWS", "고급")})
                with patch.object(api, "get_recommendations") as recs:
                    beyond = self.send(state, "그보다 더 어려운 걸로", mode)
                self.assertFalse(beyond.courses)
                self.assertIn("고급", beyond.text)
                recs.assert_not_called()

    def test_multiple_skill_followups_offer_a_vote_only_for_complete_alternatives(self):
        pair = ("single", "reviewed")
        rows = [{"skill": skill, "postings": 3, "required": 2} for skill in ("AWS", "EC2")]
        for ec2_has_demand in (True, False):
            observed = {"total_postings": 4, "skills": rows if ec2_has_demand else rows[:1]}
            state = chat.Conversation(last_course_job="cloud_engineer",
                                      last_course_levels={"AWS": ["기초"], "EC2": ["중급"]})
            with self.subTest(ec2_has_demand=ec2_has_demand), \
                    patch("backend.experiments.pick_pair", return_value=pair), \
                    patch.object(api, "get_demand", return_value=observed):
                harder = self.send(state, "더 어려운 걸로 추천해줘")
                self.assertEqual({(c["gap_skill"], c["level"]) for c in harder.courses},
                                 {("AWS", "중급"), ("EC2", "고급")})
                self.assertEqual(bool(harder.alternatives), ec2_has_demand)
                self.assertEqual(harder.text.count("더 도움이 되는 쪽"), int(ec2_has_demand))
                intermediate = self.send(state, "이번에는 중급 과정만 추천해줘")
                self.assertEqual({(c["gap_skill"], c["level"]) for c in intermediate.courses},
                                 {("AWS", "중급"), ("EC2", "중급")})
                self.assertEqual(intermediate.text.count("더 도움이 되는 쪽"), int(ec2_has_demand))


class CourseRatingPassThroughTest(unittest.TestCase):
    """평점은 선택 필드다(2026-11-20). 올바른 값만 화면으로 넘기고, 이상한 값은 과정은 두고 평점만 뺀다."""

    def test_valid_rating_is_kept_and_invalid_rating_is_dropped(self):
        demand = {"postings": 3, "of": 4, "required_ratio": 0.5}
        base = {"title": "예시 과정", "url": "https://example.com/course", "level": "기초", "prerequisites": "없음",
                "reason": "예시 이유", "match": "certain"}
        ratings = [{"rating_avg": 4.6, "rating_count": 1280}, {}, {"rating_avg": "4.6", "rating_count": 10},
                   {"rating_avg": 4.6}, {"rating_avg": 6.0, "rating_count": 3}, {"rating_avg": 4.0, "rating_count": 0},
                   {"rating_avg": True, "rating_count": True}]
        recs = [{"gap_skill": "Docker", "demand": demand, "courses": [{**base, **r} for r in ratings]}]
        shown = chat._verified_courses(recs, [{"skill": "Docker", **demand}])
        self.assertEqual(len(shown), len(ratings))
        self.assertEqual((shown[0]["rating_avg"], shown[0]["rating_count"]), (4.6, 1280))
        for course in shown[1:]:
            self.assertNotIn("rating_avg", course)
            self.assertNotIn("rating_count", course)
            self.assertEqual(course["gap_skill"], "Docker")

    def test_course_info_is_kept_when_valid_and_dropped_when_malformed(self):
        """소요 시간·언어·AWS 서비스도 선택 필드다(2026-11-21). 형식이 틀린 값만 빼고 과정은 보여 준다."""
        demand = {"postings": 3, "of": 4, "required_ratio": 0.5}
        base = {"title": "예시 과정", "url": "https://example.com/course", "level": "기초", "prerequisites": "없음",
                "reason": "예시 이유", "match": "certain"}
        good = {"duration_min": 90, "language": "English", "aws_services": ["Amazon EC2", "Amazon S3"]}
        bad = {"duration_min": "90", "language": "", "aws_services": "Amazon EC2"}
        recs = [{"gap_skill": "Docker", "demand": demand, "courses": [{**base, **good}, {**base, **bad}, dict(base)]}]
        shown = chat._verified_courses(recs, [{"skill": "Docker", **demand}])
        self.assertEqual({k: shown[0][k] for k in good}, good)
        for course in shown[1:]:
            self.assertFalse(set(good) & set(course))


if __name__ == "__main__":
    unittest.main()
