"""상담 한 턴이 수행하는 작업의 범위와 프로필 변경을 확인한다."""
import unittest
from unittest.mock import patch

from ai.chat import Turn
from backend import chat


class ChatTest(unittest.TestCase):
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

    def send(self, turn, text="현재 상태", nickname="", skills=()):
        self.intake.return_value = turn
        return chat.handle_message(self.state, text, job="cloud_engineer",
                                   sidebar_skills=list(skills), nickname=nickname)

    def test_blocked_input_never_reaches_context_or_tools(self):
        self.guard.return_value = False
        reply = self.send(Turn(), nickname="테스트")
        self.assertTrue(reply.text)
        for fn in (self.intake, self.read, self.write, self.gap, self.recs):
            fn.assert_not_called()

    def test_draft_and_summary_merge_only_confirmed_facts(self):
        self.send(Turn(add_skills=["도커"], add_experiences=["API 직접 배포"]), nickname="A")
        self.send(Turn(status="incomplete"), nickname="A")
        self.send(Turn(remove_skills=["Linux"]), nickname="A")
        reply = self.send(Turn(action="summary"), nickname="A", skills=["Git", "Linux"])
        self.assertIn("Docker", reply.text)
        self.assertIn("Git", reply.text)
        self.assertIn("API 직접 배포", reply.text)
        self.assertNotIn("Linux", reply.text)
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.write.assert_not_called()

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
        self.send(Turn(action="gap", experience_skills=["Docker"]), nickname="A")
        self.gap.assert_called_with("cloud_engineer", ["Docker"])
        self.send(Turn(action="gap", remove_skills=["Docker"], experience_skills=["Docker"]), nickname="A")
        self.gap.assert_called_with("cloud_engineer", [])
        self.write.assert_not_called()

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
        self.recs.assert_called_once_with("cloud_engineer", ["Docker"], review=True)

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
        unavailable = self.send(Turn(action="courses"))
        self.assertIn("수요", unavailable.text)
        self.assertNotIn("내부 오류", unavailable.text)
        self.assertEqual(self.recs.call_count, 2)

    def test_save_is_an_independent_scoped_write(self):
        self.send(Turn(add_skills=["Docker"], add_experiences=["Docker로 API 배포"]), nickname="A")
        self.write.assert_not_called()
        self.write.return_value = {"job": None, "skills": ["Linux"], "experiences": ["Docker로 API 배포"]}
        self.gap.return_value = [{"skill": "Linux", "owned": True, "postings": 2, "of": 2}]
        reply = self.send(Turn(action="gap", save=True, save_experiences=["Docker로 API 배포"]),
                          "Docker 배포 경험을 저장하고 부족한 역량도 봐줘", nickname="A")
        self.assertIn("저장", reply.text)
        self.gap.assert_called_once()
        self.recs.assert_not_called()
        args, changes = self.write.call_args
        self.assertEqual(args, ("A",))
        self.assertEqual(list(changes["add_experiences"]), ["Docker로 API 배포"])
        self.assertFalse(changes.get("add_skills"))
        self.assertFalse(changes.get("job"))

    def test_no_save_of_unknown_fact_or_blocked_job_and_switch_identity(self):
        self.send(Turn(add_skills=["Docker"]), nickname="A")
        self.send(Turn(save=True, save_skills=["Kubernetes"]), nickname="A")
        self.send(Turn(status="unsupported", action="courses", save=True,
                       save_skills=["Docker"]), nickname="A")
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.write.assert_not_called()
        reply = self.send(Turn(action="summary"), nickname="B")
        self.assertNotIn("Docker", reply.text)

    def test_cannot_infer_cloud_after_an_unsupported_target(self):
        self.send(Turn(status="unsupported", action="courses"), "AI 직무에 맞는 과정은?")
        self.send(Turn(action="courses"), "그래서 어떤 교육과정이 있어?")
        self.gap.assert_not_called()
        self.recs.assert_not_called()
        self.gap.return_value = [{"skill": "Docker", "owned": True, "postings": 2, "of": 3}]
        self.send(Turn(job="cloud_engineer", action="courses"), "그럼 클라우드 기준으로 추천해줘")
        self.gap.assert_called_once()
        self.recs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
