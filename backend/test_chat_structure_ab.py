"""과정 추천 턴에서 기본 구조와 3번 팀장 구조를 A/B 로 나란히 내는지 확인한다(2026-11-20).

실행: 저장소 루트에서 `python -m backend.test_chat_structure_ab`
"""
import os
import unittest
from unittest.mock import patch

from backend import chat, experiments

ASK = "먼저 들을 교육과정을 추천해줘"


class ChatStructureABTest(unittest.TestCase):
    def send(self, structures: str, text: str = ASK, state=None):
        env = {"BEDROCK_MOCK": "1", "USAGE_LOG": "off", "AB_STRUCTURES": structures, "EXPERIMENT_MODELS": "",
               "FEEDBACK_LOG": os.devnull}
        with patch.dict(os.environ, env):
            state = state or chat.Conversation()
            return state, chat.handle_message(state, text, job="cloud_engineer", sidebar_skills=[], sidebar_levels={})

    def test_setting_turns_on_only_with_the_default_structure(self):
        for value, expected in (("reviewed,3", "3"), ("3,reviewed", "3"), ("3", None), ("single,reviewed", None), ("", None)):
            with patch.dict(os.environ, {"AB_STRUCTURES": value}):
                self.assertEqual(experiments.chat_rival(), expected, value)
        with patch.dict(os.environ, {"AB_STRUCTURES": "reviewed,3", "EXPERIMENT_MODELS": ""}):
            self.assertTrue(experiments.active())      # 화면이 선택을 받는다
            self.assertEqual(experiments.chat_architecture(), "screen")  # 상담 자체는 기본 구조로 처리한다
            self.assertEqual(experiments.structures(), [])  # 기술별 추천 구조 A/B 와 겹치지 않는다

    def test_course_turn_shows_both_structures_and_keeps_the_default_answer(self):
        state, reply = self.send("reviewed,3")
        self.assertEqual(reply.action, "courses")
        self.assertEqual(sorted(side["structure"] for side in reply.alternatives), ["3", "reviewed"])
        default = next(side for side in reply.alternatives if side["structure"] == "reviewed")
        self.assertEqual(default["courses"], reply.courses)  # 화면의 기본 답은 기본 구조의 것
        for side in reply.alternatives:
            self.assertTrue(side["courses"])
            self.assertTrue(all(card["reason"] and card["url"].startswith("https://") for card in side["courses"]))
            self.assertEqual(set(side["tokens"]), {"input", "output"})
        self.assertEqual(len(state.history), len(self.send("")[0].history))  # 대화 기록은 비교를 끈 때와 같다(한 턴만 남는다)

    def test_other_turns_and_switched_off_setting_show_no_comparison(self):
        self.assertFalse(self.send("reviewed,3", "지금 뭐가 부족해?")[1].alternatives)
        self.assertFalse(self.send("")[1].alternatives)

    def test_failed_rival_leaves_the_default_answer_alone(self):
        with patch("ai.supervisor.interpret_turn", side_effect=RuntimeError("boom")):
            _, reply = self.send("reviewed,3")
        self.assertTrue(reply.courses)
        self.assertFalse(reply.alternatives)

    def test_rival_never_writes_to_the_profile(self):
        with patch("backend.chat_profiles.save_profile", create=True) as save, \
                patch("backend.chat._save", wraps=chat._save) as saver:
            self.send("reviewed,3")
        save.assert_not_called()
        saver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
