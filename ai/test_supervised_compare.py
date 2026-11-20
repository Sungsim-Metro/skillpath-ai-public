"""Regression tests for the multi-turn architecture comparison."""

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai import bedrock
from ai.eval import supervised_compare as compare


class ComparisonTest(unittest.TestCase):
    def test_pinned_agent_prompt_and_fixture_run_against_current_backend(self):
        available = subprocess.run(["git", "cat-file", "-e", compare.BASELINE], cwd=compare.ROOT,
                                   stderr=subprocess.DEVNULL)
        if available.returncode:
            self.skipTest("Integration test requires the pinned PR #121 Git object")
        with TemporaryDirectory() as temp, patch.dict("os.environ", BEDROCK_MOCK="1", USAGE_LOG="off"):
            agent = compare.pinned_agent(Path(temp))
            self.assertEqual(agent.PROMPT_VERSION, "agent_v1.2")
            self.assertIn("recommend_courses", agent.TOOL_FUNCS)
            self.assertNotIn("search_courses", agent.TOOL_FUNCS)
            state = []
            with compare.TurnProbe() as probe:
                row = compare.agent_turn(agent, state, "Python과 Docker를 사용했어요. 뭐부터 공부할까요?", probe)
            self.assertIn("recommend_courses", row["tools"])
            self.assertEqual(probe.targets, ["AWS", "Linux"])
            self.assertEqual(probe.mock_calls, 3)
            self.assertEqual([message["role"] for message in state], ["user", "assistant"])
            with patch.object(agent, "chat", wraps=agent.chat) as chat, compare.TurnProbe() as probe:
                compare.agent_turn(agent, state, "다시 설명해 주세요.", probe)
                self.assertEqual(chat.call_args.args[1], state[:2])

    def test_exact_evidence_checks_each_visible_card_and_cannot_pass_vacuously(self):
        source = {"title": "Container basics", "url": "https://example.org/docker", "match": "uncertain",
                  "reason": "Verified objective.", "gap_skill": "Docker"}
        self.assertTrue(compare.card_evidence([source], [source]))
        self.assertFalse(compare.card_evidence([{**source, "match": "certain"}], [source]))
        self.assertFalse(compare.card_evidence([{**source, "reason": "A new reason."}], [source]))
        self.assertFalse(compare.card_evidence([], [source]))

    def test_mock_fixture_observations_do_not_become_model_accuracy(self):
        rows = [{"checks": {"explicit_target": True}, "fixture": "default",
                 "calls": 0, "mock_calls": 1, "tokens": None, "seconds": 0.01}]
        result = compare.summarize(rows, mock=True, method="1")
        self.assertEqual(result["checks"]["explicit_target"], {"passed": 1, "applicable": 1})
        self.assertEqual(result["fixture_keys"], {"default": 1})
        self.assertIsNone(result["model_accuracy"])
        self.assertIsNone(result["tokens"])

    def test_attempt_budget_precedes_any_network_call(self):
        budget = compare.CallBudget(1)
        self.assertIsNone(budget.take())
        with self.assertRaises(compare.BudgetExceeded):
            budget.take()
        self.assertEqual(budget.used, 1)

    def test_only_interrupted_arm_can_be_run_again(self):
        case = {"id": "single", "turns": [{"text": "저는 Docker를 배우고 싶어요.", "expect": {"no_courses": True}}]}
        with patch.dict("os.environ", BEDROCK_MOCK="1", USAGE_LOG="off"):
            reports, interrupted = compare.evaluate([case], None, (None, None, None), methods=("3",))
        self.assertIsNone(interrupted)
        self.assertEqual(list(reports), ["3"])
        self.assertTrue(reports["3"]["rows"][0]["checks"]["no_premature_courses"])

    def test_first_reference_uses_user_visible_order(self):
        reply = "AWS도 언급됩니다.\n- Linux · 공고 수요가 높습니다.\n- AWS · 다음 항목입니다."
        self.assertEqual(compare.first_visible_gap(reply, ["AWS", "Linux"]), "Linux")
        self.assertIsNone(compare.first_visible_gap("AWS도 언급됩니다.", ["AWS", "Linux"]))

    def test_budget_interrupt_keeps_prior_and_partial_turn_costs(self):
        messages = [{"text": "first", "expect": {"no_courses": True}},
                    {"text": "second", "expect": {"no_courses": True}}]
        response = dict(text="", cards=[], tools=[], owned=set(), decisions=[], action="note",
                        issue=None, fixture=None)
        with patch.object(compare, "supervised_turn", side_effect=[response, compare.BudgetExceeded("model")]):
            rows = compare.run_case({"id": "cap", "turns": messages}, "3", None, (None, None, None))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["issue"], "budget_model")
        self.assertEqual(rows[-1]["checks"], {})

    def test_baseline_preserves_reason_after_its_no_demand_tool_filter(self):
        source = {"gap_skill": "EC2", "title": "Compute basics", "reason": "40건 중 0건. 배우기."}
        filtered = {**source, "reason": "공고 수 정보는 없습니다. 배우기."}
        probe = compare.TurnProbe()
        probe.targets, probe.reviewed, probe.source = ["EC2"], [True], [source]
        actual = dict(text=f"{filtered['title']} · {filtered['reason']}", cards=[], tools=[],
                      decisions=[], action="recommend_courses", tool_courses=[filtered])
        checks = compare.check_turn({"explicit_target": ["EC2"]}, actual, probe, None, "1")
        self.assertTrue(checks["verbatim_reviewed_delivery"])

    def test_missing_source_does_not_skip_evidence_expectation(self):
        actual = dict(text="완료.", cards=[], tools=[], decisions=[], action="answer")
        checks = compare.check_turn({"explicit_target": ["Docker"]}, actual, compare.TurnProbe(), None, "1")
        self.assertFalse(checks["verbatim_reviewed_delivery"])

    def test_worker_failure_keeps_accepted_target_and_counts_zero_delivery(self):
        def failed(state, text):
            from ai import guardrail
            from backend import api
            guardrail.check_input(text)
            api.get_recommendations("cloud_engineer", ["Docker"], review=True)
            return dict(text="", cards=[], tools=[], decisions=[], action="courses", owned=None,
                        issue="reply_failed", fixture=None)

        with (patch.dict("os.environ", BEDROCK_MOCK="1"),
              patch("backend.api.get_recommendations", return_value=[]),
              patch.object(compare, "supervised_turn", failed)):
            case = {"id": "failed", "turns": [{"text": "ok", "expect": {"explicit_target": ["Docker"]}}]}
            row, = compare.run_case(case, "3", None, (None, None, None))
        self.assertTrue(row["checks"]["explicit_target"])
        self.assertFalse(row["checks"]["verbatim_reviewed_delivery"])
        self.assertNotIn("verbatim_among_returned", row["checks"])

    def test_failed_reply_cannot_pass_by_omitting_courses(self):
        def failed(state, text):
            from ai import guardrail
            guardrail.check_input(text)
            return dict(text="", cards=[], tools=[], decisions=[], action="intake", owned=None,
                        issue="reply_failed", fixture=None)

        with patch.dict("os.environ", BEDROCK_MOCK="1"), patch.object(compare, "supervised_turn", failed):
            case = {"id": "failed", "turns": [{"text": "ok", "expect": {"no_courses": True}}]}
            row, = compare.run_case(case, "3", None, (None, None, None))
            self.assertFalse(row["checks"]["no_premature_courses"])

    def test_common_cache_is_isolated_between_cases(self):
        from backend import api
        with compare.cold_case():
            api._recommend_cache["only_in_one_arm"] = [{}]
        with compare.cold_case():
            self.assertFalse(api._recommend_cache)

    def test_local_catalog_lookups_also_start_cold(self):
        from ai import recommend
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "courses.csv"
            catalog.write_text("course_id,title\n1,Example\n", encoding="utf-8")
            with compare.cold_case(), patch.object(recommend, "COURSES_CSV", catalog):
                recommend._courses()
                self.assertGreater(recommend._courses.cache_info().currsize, 0)
            with compare.cold_case():
                self.assertEqual(recommend._courses.cache_info().currsize, 0)

    def test_probe_restores_backend_after_failure(self):
        from backend import api
        original = (api.get_recommendations, bedrock._call_tools)
        with self.assertRaises(RuntimeError), compare.TurnProbe():
            raise RuntimeError("test")
        self.assertEqual(original, (api.get_recommendations, bedrock._call_tools))

    def test_worker_error_records_stage_and_type_without_response_text(self):
        from ai import recommend_review
        from backend import api

        def failed_recommendation(*args, **kwargs):
            return recommend_review.converse_json("system", "request", mock_key="recommend_reviews")

        with (patch.object(api, "get_recommendations", side_effect=failed_recommendation),
              patch.object(recommend_review, "converse_json", side_effect=bedrock.BedrockJSONError("private body")),
              compare.TurnProbe() as probe):
            with self.assertRaises(bedrock.BedrockJSONError):
                api.get_recommendations("cloud_engineer", ["Kubernetes"], review=True)
        self.assertEqual(probe.worker_errors, ["recommend_reviews/BedrockJSONError"])


if __name__ == "__main__":
    unittest.main()
