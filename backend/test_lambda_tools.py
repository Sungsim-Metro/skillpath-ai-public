"""Exercise the application boundary with serialized Lambda requests and responses."""
import json
import os
import unittest
from collections import OrderedDict
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from unittest.mock import Mock, patch
from uuid import UUID

from ai import bedrock, usage
from backend import api, chat, experiments, lambda_tools
from backend.jobs import DEFAULT_JOB, JOBS


class LambdaToolsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "0", "USAGE_LOG": "off",
            lambda_tools.DEMAND_FUNCTION: "demand", lambda_tools.RECOMMEND_FUNCTION: "recommend",
            "DYNAMODB_DEMAND_TABLE": "test-demand-lambda",
        }))
        self.requests = []
        self.logs = self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(patch.object(api, "_recommend_cache", OrderedDict()))
        self.enterContext(patch.object(api, "_demand_cache", {}))
        self.client = self.enterContext(patch.object(lambda_tools, "_client")).return_value
        self.client.invoke.side_effect = self.dispatch

    def summaries(self):
        return [json.loads(line) for line in self.logs.getvalue().splitlines()]

    def dispatch(self, *, FunctionName, InvocationType, Payload):
        self.assertEqual(InvocationType, "RequestResponse")
        event = json.loads(Payload)
        self.requests.append((FunctionName, event))
        handler = {"demand": lambda_tools.demand_handler, "recommend": lambda_tools.recommend_handler}[FunctionName]
        return {"StatusCode": 200, "Payload": BytesIO(json.dumps(handler(event, None)).encode())}

    def test_demand_and_gap_match_the_existing_api_for_both_roles(self):
        store = self.enterContext(patch.object(api.DynamoDBStore, "from_env")).return_value
        store.get_demand_snapshot.side_effect = lambda job: {"demand": api._load("demand.json", job)}
        for job in JOBS:
            for owned in (["Linux"], {"custom:Linux": 2}, None):
                with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
                    expected_demand = api.get_demand(job)
                    expected_gap = api.get_gap(job, owned)
                self.assertEqual(api.get_demand(job), expected_demand)
                self.assertEqual(api.get_gap(job, owned), expected_gap)
        self.assertEqual(len(self.requests), len(JOBS) * 3 * 2)

    def test_review_and_experiment_receive_model_options_and_return_usage(self):
        levels = ("Intermediate",)
        structure = "single"
        calls = []

        def build(skill, demand, **options):
            calls.append((skill, demand, options, bedrock.current_model()))
            usage.record("recommend", 1, {"inputTokens": 9, "outputTokens": 4}, False,
                         model_id=bedrock.current_model())
            return {"gap_skill": skill, "demand": demand, "courses": []}, None

        self.enterContext(patch("ai.recommend.build_recommendation", side_effect=build))
        session = usage.SessionUsage()
        with bedrock.use_model("test-experimental-model"), usage.track_session(session):
            rows = api.get_recommendations(DEFAULT_JOB, ["test-tool-skill"], review=True,
                                           review_evidence={}, course_levels=levels)
            compared = experiments.recommend_with(structure, DEFAULT_JOB, ["test-tool-skill"],
                                                  evidence={"test-tool-skill": None}, course_levels=levels)
            self.assertEqual(api.get_recommendations(DEFAULT_JOB, ["test-tool-skill"], review=True,
                                                     review_evidence={}, course_levels=levels), rows)
        self.assertEqual(rows, compared)
        self.assertEqual([item[2] for item in calls], [
            {"review": True, "course_levels": levels}, {"course_levels": levels},
        ])
        self.assertEqual([item[3] for item in calls], ["test-experimental-model"] * 2)
        self.assertEqual((session.input_tokens, session.output_tokens), (18, 8))
        first, comparison, cached = self.summaries()
        self.assertEqual((first["operation"], comparison["operation"]), ("get_recommendations", "recommend_with"))
        self.assertEqual((first["model_responses"], first["input_tokens"], first["output_tokens"]), (1, 9, 4))
        self.assertEqual((cached["cache_hits"], cached["model_responses"]), (["recommendations"], 0))

    def test_failed_remote_review_keeps_usage_and_sanitizes_errors(self):
        def fail(*args, **kwargs):
            usage.record("review", 1, {"inputTokens": 11}, False)
            raise ValueError("sensitive model output")

        self.enterContext(patch("ai.recommend.build_recommendation", side_effect=fail))
        session = usage.SessionUsage()
        with usage.track_session(session), self.assertRaisesRegex(ValueError, "Lambda tool") as error:
            api.get_recommendations(DEFAULT_JOB, ["failed-tool-skill"], review=True, review_evidence={})
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual((session.input_tokens, session.output_tokens), (11, 0))
        self.assertEqual(len(self.requests), 1)
        summary, = self.summaries()
        self.assertEqual((summary["success"], summary["input_tokens"], summary["error"]), (False, 11, "validation"))
        self.assertNotIn("sensitive", self.logs.getvalue())

    def test_runtime_errors_and_mock_mode_never_trigger_fallback_or_double_invocation(self):
        self.client.invoke.side_effect = None
        self.client.invoke.return_value = {
            "StatusCode": 200, "FunctionError": "Unhandled", "Payload": BytesIO(b"private error details"),
        }
        store = self.enterContext(patch.object(api.DynamoDBStore, "from_env", new=Mock()))
        with self.assertRaisesRegex(RuntimeError, "failed to execute"):
            api.get_demand(DEFAULT_JOB)
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
            self.assertEqual(api.get_demand(DEFAULT_JOB), api._load("demand.json"))
        store.assert_not_called()
        self.client.invoke.assert_called_once()

    def test_each_handler_only_runs_its_own_operations(self):
        bad = {"operation": "private unknown operation", "turn_id": "private user identity",
               "arguments": {"job_category": DEFAULT_JOB}}
        self.assertEqual(lambda_tools.demand_handler(bad, None), {"error": "validation", "usage": []})
        summary, = self.summaries()
        self.assertEqual((summary["operation"], summary["turn_id"]), ("unknown", summary["request_id"]))
        self.assertEqual(UUID(summary["turn_id"]).version, 4)
        self.assertNotIn("private", self.logs.getvalue())

    def test_both_chat_structures_forward_the_same_turn_id(self):
        def run_turn(*args, **kwargs):
            api.get_demand(DEFAULT_JOB)
            return chat.Reply("private reply", action="courses", courses=[{"gap_skill": "AWS", "level": "기초"}])

        store = self.enterContext(patch.object(api.DynamoDBStore, "from_env")).return_value
        store.get_demand_snapshot.return_value = {"demand": api._load("demand.json")}
        with (patch.object(chat, "_handle_turn", side_effect=run_turn), patch.object(chat, "_record_turn"),
              patch.object(experiments, "chat_rival", return_value="3")):
            reply = chat.handle_message(chat.Conversation(), "private input", job=DEFAULT_JOB, sidebar_skills=[])
        api.get_demand(DEFAULT_JOB)
        first, rival, outside_chat = self.summaries()
        self.assertEqual(first["turn_id"], reply.turn_id)
        self.assertEqual(rival["turn_id"], reply.turn_id)
        self.assertNotEqual(first["request_id"], rival["request_id"])
        self.assertEqual((first["cache_hits"], rival["cache_hits"]), ([], ["demand"]))
        self.assertEqual(outside_chat["turn_id"], outside_chat["request_id"])
        self.assertNotIn("private", self.logs.getvalue())
