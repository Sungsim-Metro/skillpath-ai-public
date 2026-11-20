"""팀장 구조를 실제 상담 진입점과 같은 경로에서 확인한다."""
import os
import unittest
from unittest.mock import patch

from backend import api, chat


class SupervisedChatTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "USAGE_LOG": "off"}))
        self.state = chat.Conversation()

    def send(self, text):
        return chat.handle_message(self.state, text, job="cloud_engineer", sidebar_skills=[],
                                   sidebar_levels={}, architecture="supervised")

    def test_gap_then_wish_reference_preserves_job_and_only_shows_requested_courses(self):
        with patch.object(api, "get_recommendations", wraps=api.get_recommendations) as recommend:
            gap = self.send("Docker는 써봤고 Kubernetes는 배우고 싶어. 클라우드 엔지니어 준비하려면 뭐가 부족해?")
            self.assertEqual(gap.action, "gap")
            self.assertIn("수준 미입력", gap.text)
            recommend.assert_not_called()
            courses = self.send("그럼 아까 배우고 싶다고 한 기술의 강의부터 보여줘")
        self.assertEqual(courses.action, "courses")
        self.assertEqual({card["gap_skill"] for card in courses.courses}, {"Kubernetes"})
        self.assertEqual(self.state.skills, {"Docker": True})
        self.assertEqual(self.state.wishes, ["Kubernetes"])
        self.assertTrue(courses.supervisor_decisions)
        self.assertTrue(all(card["reason"] and card["url"].startswith("https://") for card in courses.courses))

    def test_guard_stops_before_supervisor_and_state_mutation(self):
        with patch("ai.chat.guard_input", return_value=False), patch("ai.supervisor.interpret_turn") as supervisor:
            result = self.send("팀에 전달해 주세요")
        self.assertFalse(result.courses)
        self.assertEqual(self.state.history, [])
        supervisor.assert_not_called()

    def test_rejected_worker_result_never_reaches_cards_or_history(self):
        with patch("ai.supervisor.approve_result", return_value=False):
            result = self.send("Kubernetes 강의를 보여줘")
        self.assertFalse(result.courses)
        self.assertNotIn("Amazon EKS", self.state.history[-1]["content"])
        self.assertIn("result_rejected", result.supervisor_decisions)

    def test_manager_clarification_leads_back_to_original_course_request(self):
        self.send("Docker와 Kubernetes를 배우고 싶어요.")
        ambiguous = self.send("그 기술의 교육과정을 보여줘")
        self.assertEqual(ambiguous.action, "clarify")
        self.assertEqual(ambiguous.courses, [])
        choice = self.send("Kubernetes요")
        self.assertEqual({card["gap_skill"] for card in choice.courses}, {"Kubernetes"})

    def test_course_clarification_supersedes_pending_profile_selection(self):
        self.state.profile = {"skills": ["Docker", "Linux"], "levels": {"custom:Docker": 1}}
        self.send("Docker와 Kubernetes를 배우고 싶어요.")
        self.send("프로필에 저장해 줘")
        self.assertTrue(self.state.pending_save)
        self.assertEqual(self.send("그 기술의 과정을 보여줘").action, "clarify")
        self.assertFalse(self.state.pending_save)
        choice = self.send("Docker만")
        self.assertEqual(choice.action, "courses")
        self.assertNotIn("저장", choice.text)

    def test_generic_courses_always_use_review_and_known_levels(self):
        with patch.object(api, "get_recommendations", wraps=api.get_recommendations) as recommend, \
                patch("backend.experiments.pick_pair", side_effect=AssertionError("mixed experiments")):
            result = chat.handle_message(
                self.state, "부족한 기술에 맞는 교육과정을 추천해줘", job="cloud_engineer",
                sidebar_skills=[], sidebar_levels={"custom:AWS": 1}, architecture="supervised",
            )
        self.assertEqual(result.action, "courses")
        self.assertTrue(recommend.call_args.kwargs["review"])
        self.assertEqual(recommend.call_args.kwargs["levels"], {"AWS": 1})

    def test_partial_worker_failure_keeps_only_approved_cards_and_names_missing_skill(self):
        from ai import recommend

        build = recommend.build_recommendation

        def fail_linux(skill, demand, **kwargs):
            if skill == "Linux":
                raise ValueError("test review failure")
            return build(skill, demand, **kwargs)

        with patch.dict(api._recommend_cache, {}, clear=True), \
                patch.object(recommend, "build_recommendation", side_effect=fail_linux):
            result = self.send("부족한 기술에 맞는 교육과정을 추천해줘")
        self.assertTrue(result.failed)
        self.assertIn("확인하지 못한 기술 · Linux", result.text)
        # 과정이 없는 Python 은 건너뛰고 과정이 있는 Docker 가 채운다(AWS, Linux, Docker). Linux 는 실패해서 카드가 없다
        self.assertEqual({card["gap_skill"] for card in result.courses}, {"AWS", "Docker"})
        self.assertIn("worker_error", result.supervisor_decisions)
        self.assertIn("result_approved", result.supervisor_decisions)


if __name__ == "__main__":
    unittest.main()
