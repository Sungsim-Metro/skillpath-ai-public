"""상담 비교 실험의 배정과 기록. 실행: python -m unittest backend.test_experiments"""
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai import bedrock
from ai.chat import ChatIntakeError, Turn
from backend import chat, experiments

COURSE = {"title": "컨테이너 입문", "url": "https://example.com/course", "prerequisites": "없음",
          "reason": "컨테이너 배포 연습", "level": "기초", "match": "certain"}
GAP = [{"skill": "Docker", "owned": False, "postings": 20, "of": 40, "required_ratio": 0.45}]


def rec(reason):
    return {"gap_skill": "Docker", "demand": {"postings": 20, "of": 40, "required_ratio": 0.45},
            "courses": [dict(COURSE, reason=reason)]}


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log = Path(self.directory.name) / "feedback.jsonl"
        self.env({"BEDROCK_MOCK": "1", "FEEDBACK_LOG": str(self.log), "USAGE_LOG": "off",
                  "EXPERIMENT_MODELS": "", "AB_STRUCTURES": ""})

    def env(self, values):
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()


class ExperimentsTest(Base):
    def test_off_by_default_and_mock_mode_never_assigns_a_model(self):
        self.assertIsNone(experiments.pick_model())
        self.assertIsNone(experiments.pick_pair())
        self.env({"EXPERIMENT_MODELS": "model-a, model-b"})
        self.assertIsNone(experiments.pick_model())          # 가짜 모드는 모델을 부르지 않는다
        self.env({"BEDROCK_MOCK": "0"})
        picks = {experiments.pick_model(random.Random(seed)) for seed in range(20)}
        self.assertEqual(picks, {"model-a", "model-b"})

    def test_pair_needs_two_registered_structures(self):
        self.env({"AB_STRUCTURES": "single"})
        self.assertIsNone(experiments.pick_pair())
        self.env({"AB_STRUCTURES": "single, nowhere"})        # 등록되지 않은 이름은 세지 않는다
        self.assertIsNone(experiments.pick_pair())
        self.env({"AB_STRUCTURES": "single,reviewed"})
        pairs = {experiments.pick_pair(random.Random(seed)) for seed in range(20)}
        self.assertEqual(pairs, {("single", "reviewed"), ("reviewed", "single")})  # 좌우 순서도 무작위

    def test_use_model_sends_and_logs_that_model(self):
        usage_log = Path(self.directory.name) / "usage.jsonl"
        self.env({"BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "default-model", "USAGE_LOG": str(usage_log)})
        answer = {"output": {"message": {"content": [{"text": json.dumps({"ok": True})}]}},
                  "usage": {"inputTokens": 3, "outputTokens": 2}}
        client = self.start("ai.bedrock._client")
        client.return_value.converse.return_value = answer
        with bedrock.use_model("cheap-model"):
            bedrock.converse_json("s", "u", mock_file="", mock_key="k", usage_stage="chat_intake")
        bedrock.converse_json("s", "u", mock_file="", mock_key="k", usage_stage="chat_intake")
        sent = [call.kwargs["modelId"] for call in client.return_value.converse.call_args_list]
        self.assertEqual(sent, ["cheap-model", "default-model"])   # 범위를 벗어나면 기본 모델로 돌아온다
        logged = [json.loads(line)["model"] for line in usage_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(logged, ["cheap-model", "default-model"])

    def test_feedback_round_trip_keeps_no_content(self):
        experiments.save_feedback("s1", "t1", job="cloud_engineer", action="gap", model="m", rating="up")
        experiments.save_feedback("s1", "t1", rating="down")      # 고쳐 누르면 나중 값이 남는다
        experiments.save_feedback("s1", "t2", left="single", right="reviewed", picked="reviewed",
                                  tokens={"single": {"input": 10, "output": 5}})
        rows = {row["turn_id"]: row for row in experiments.load_feedback()}
        self.assertEqual(rows["t1"]["rating"], "down")
        self.assertEqual(rows["t1"]["model"], "m")
        self.assertEqual(rows["t2"]["picked"], "reviewed")
        for bad in ({"text": "원문"}, {"nickname": "A"}, {"rating": "great"}):
            with self.assertRaises(ValueError):
                experiments.save_feedback("s1", "t3", **bad)
        self.assertNotIn("원문", self.log.read_text(encoding="utf-8"))

    def test_real_mode_writes_one_item_per_turn(self):
        self.env({"BEDROCK_MOCK": "0", "DYNAMODB_FEEDBACK_TABLE": "feedback"})
        table = MagicMock()
        self.start("backend.dynamodb._resource").return_value.Table.return_value = table
        experiments.save_feedback("s1", "t1", model="m", rating="up")
        call = table.update_item.call_args.kwargs
        self.assertEqual(call["Key"], {"session_id": "s1", "turn_id": "t1"})
        self.assertEqual(set(call["ExpressionAttributeNames"].values()), {"model", "rating", "ts"})
        self.env({"DYNAMODB_FEEDBACK_TABLE": ""})
        with self.assertRaises(ValueError):
            experiments.save_feedback("s1", "t1", rating="up")


class SummaryTest(Base):
    PRICING = {"currency": "USD", "models": {"big": {"input_per_1k": 0.003, "output_per_1k": 0.015},
                                             "small": {"input_per_1k": 0.001, "output_per_1k": 0.005}}}

    def rows(self, model, ups, downs, unrated=0, failed=0):
        turn = {"model": model, "turn_tokens": {"input": 1000, "output": 200}}
        return ([dict(turn, rating="up")] * ups + [dict(turn, rating="down")] * downs + [dict(turn)] * unrated
                + [dict(turn, failed=True)] * failed)

    def test_models_are_counted_and_costed_per_turn(self):
        rows = self.rows("big", 18, 2, unrated=5) + self.rows("small", 17, 3, failed=2)
        big, small = experiments.summarize_models(rows, self.PRICING)
        self.assertEqual((big["turns"], big["rated"], big["up_ratio"], big["failed"]), (25, 20, 0.9, 0))
        self.assertEqual((small["turns"], small["rated"], small["up_ratio"], small["failed"]), (22, 20, 0.85, 2))
        self.assertAlmostEqual(big["avg_cost"], 0.006)                 # 1000/1000*0.003 + 200/1000*0.015
        self.assertAlmostEqual(small["avg_cost"], 0.002)

    def test_cheaper_model_wins_only_inside_the_tolerance(self):
        close = experiments.summarize_models(self.rows("big", 18, 2) + self.rows("small", 17, 3), self.PRICING)
        decision = experiments.decide_model(close)
        self.assertEqual((decision["status"], decision["model"]), ("decided", "small"))   # 5%p 차이 안: 싼 쪽
        self.assertAlmostEqual(decision["saving_vs_priciest"], 0.667)
        far = experiments.summarize_models(self.rows("big", 18, 2) + self.rows("small", 14, 6), self.PRICING)
        self.assertEqual(experiments.decide_model(far)["model"], "big")                    # 차이가 크면 평가가 높은 쪽
        few = experiments.summarize_models(self.rows("big", 3, 0) + self.rows("small", 3, 0), self.PRICING)
        self.assertEqual(experiments.decide_model(few)["status"], "provisional")           # 평가가 적으면 참고용
        one = experiments.summarize_models(self.rows("big", 3, 0), self.PRICING)
        self.assertEqual(experiments.decide_model(one)["status"], "insufficient")

    def test_structures_count_choices_and_ties(self):
        pick = {"left": "single", "right": "reviewed", "tokens": {"single": {"input": 100, "output": 10},
                                                                   "reviewed": {"input": 300, "output": 30}}}
        rows = [dict(pick, picked="reviewed")] * 3 + [dict(pick, picked="single")] + [dict(pick, picked=experiments.TIE)]
        reviewed, single = experiments.summarize_structures(rows + [{"model": "big", "rating": "up"}])
        self.assertEqual((reviewed["shown"], reviewed["picked"], reviewed["ties"], reviewed["pick_ratio"]), (5, 3, 1, 0.6))
        self.assertEqual((single["picked"], single["avg_input_tokens"]), (1, 100))

    def test_decision_is_saved_with_the_numbers_it_was_based_on(self):
        summary = experiments.summarize_models(self.rows("big", 18, 2) + self.rows("small", 17, 3), self.PRICING)
        experiments.save_decision(summary, experiments.decide_model(summary))
        saved = experiments.load_decisions()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["decision"]["model"], "small")
        self.assertEqual([m["model"] for m in saved[0]["models"]], ["big", "small"])
        self.assertEqual(experiments.load_feedback(), [])                # 결정 기록은 평가 기록에 섞이지 않는다


class ChatExperimentTest(Base):
    """상담 한 턴에 실험이 붙는 방식."""

    def setUp(self):
        super().setUp()
        self.start("ai.chat.guard_input", return_value=True)
        self.start("backend.api.get_gap", return_value=GAP)
        self.intake = self.start("ai.chat.interpret")
        self.recs = self.start("backend.api.get_recommendations", return_value=[rec("기본 추천")])
        self.state = chat.Conversation()

    def send(self, turn):
        self.intake.return_value = turn
        return chat.handle_message(self.state, "과정 추천해줘", job="cloud_engineer", sidebar_skills=[])

    def test_without_settings_the_turn_is_unchanged(self):
        reply = self.send(Turn(action="courses"))
        self.assertEqual([c["reason"] for c in reply.courses], ["기본 추천"])
        self.assertEqual((reply.alternatives, reply.action, reply.failed), ([], "courses", False))
        self.assertTrue(reply.turn_id)
        self.recs.assert_called_once_with("cloud_engineer", ["Docker"], review=True)

    def test_two_structures_are_shown_side_by_side_with_the_same_checks(self):
        built = {"single": [rec("한 번에 쓴 이유")], "reviewed": [rec("검토한 이유")]}
        self.start("backend.experiments.pick_pair", return_value=("single", "reviewed"))
        made = self.start("backend.experiments.recommend_with", side_effect=lambda name, job, skills: built[name])
        reply = self.send(Turn(action="courses"))
        self.assertEqual([a["structure"] for a in reply.alternatives], ["single", "reviewed"])
        self.assertEqual([a["courses"][0]["reason"] for a in reply.alternatives], ["한 번에 쓴 이유", "검토한 이유"])
        self.assertEqual(reply.courses[0]["reason"], "검토한 이유")     # 기본으로 보여 줄 것은 검토한 쪽
        self.assertEqual(reply.courses[0]["gap_skill"], "Docker")
        self.assertEqual(made.call_count, 2)
        self.recs.assert_not_called()
        self.assertIn("더 도움이 되는 쪽", reply.text)

    def test_one_failed_structure_falls_back_to_the_other_without_a_choice(self):
        def build(name, job, skills):
            if name == "single":
                raise ValueError("검사 실패")
            return [rec("검토한 이유")]

        self.start("backend.experiments.pick_pair", return_value=("single", "reviewed"))
        made = self.start("backend.experiments.recommend_with", side_effect=build)
        reply = self.send(Turn(action="courses"))
        self.assertEqual(reply.alternatives, [])
        self.assertEqual(reply.courses[0]["reason"], "검토한 이유")
        self.assertFalse(reply.failed)
        empty = {"gap_skill": "Docker", "demand": rec("")["demand"], "courses": []}
        made.side_effect = lambda name, job, skills: [rec("한 번에 쓴 이유")] if name == "single" else [empty]
        reply = self.send(Turn(action="courses"))                      # 검토한 쪽이 비면 검토 안 한 쪽만 혼자 나가지 않는다
        self.assertEqual((reply.alternatives, reply.courses, reply.failed), ([], [], False))
        made.side_effect = ValueError("검사 실패")                      # 둘 다 실패하면 과정을 보여 주지 않는다
        reply = self.send(Turn(action="courses"))
        self.assertTrue(reply.failed)
        self.assertFalse(reply.courses)

    def test_assigned_model_covers_the_whole_turn_and_failures_are_counted(self):
        self.env({"BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "default-model", "EXPERIMENT_MODELS": "cheap-model",
                  "DYNAMODB_FEEDBACK_TABLE": "feedback"})
        seen = []
        self.intake.side_effect = lambda *a, **k: seen.append(bedrock.current_model()) or Turn(action="summary")
        save = self.start("backend.experiments.save_feedback")
        reply = chat.handle_message(self.state, "상태", job="cloud_engineer", sidebar_skills=[])
        self.assertEqual((seen, reply.model), (["cheap-model"], "cheap-model"))
        recorded = save.call_args.kwargs                               # 평가가 없어도 턴의 배정과 토큰은 남긴다
        self.assertEqual((recorded["model"], recorded["action"], recorded["failed"]), ("cheap-model", "summary", None))
        self.assertEqual(set(recorded["turn_tokens"]), {"input", "output"})
        self.intake.side_effect = ChatIntakeError("해석 실패")
        reply = chat.handle_message(self.state, "상태", job="cloud_engineer", sidebar_skills=[])
        self.assertTrue(reply.failed)
        self.assertEqual(save.call_args.kwargs["model"], "cheap-model")
        self.assertTrue(save.call_args.kwargs["failed"])
        self.assertEqual(bedrock.current_model(), "default-model")


if __name__ == "__main__":
    unittest.main()
