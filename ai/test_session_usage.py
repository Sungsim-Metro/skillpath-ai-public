"""Regression tests for token accounting across chat sessions and model calls."""

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from ai.usage import SessionUsage, record, track_session
from pipeline import embed


class SessionUsageTest(unittest.TestCase):
    def test_only_new_embeddings_count_towards_the_chat_requesting_them(self) -> None:
        first, second = SessionUsage(), SessionUsage()
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "usage.jsonl"
            with (
                patch.dict(os.environ, {"USAGE_LOG": str(log), "EMBED_MODEL_ID": "embed-model"}),
                patch.object(embed, "is_mock", return_value=False),
                patch.object(embed, "_client") as client,
                patch.object(embed, "_usage", {"calls": 0, "tokens": 0}),
                patch.object(embed, "CACHE_FILE", Path(folder) / "cache.json"),
            ):
                client.return_value.invoke_model.return_value = {
                    "body": BytesIO(b'{"embedding":[1.0],"inputTextTokenCount":7}')
                }
                with track_session(first):
                    embed.embed("example query")
                with track_session(second):
                    embed.embed("example query")
                client.return_value.invoke_model.assert_called_once()
            row = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual((row["stage"], row["model"], row["session_id"]), ("embedding", "embed-model", first.session_id))
        self.assertEqual((first.input_tokens, first.output_tokens, second.total_tokens), (7, 0, 0))

    def test_nested_sessions_and_unscoped_calls(self) -> None:
        first, second = SessionUsage(), SessionUsage()
        self.assertEqual(UUID(hex=first.session_id).version, 4)
        self.assertNotEqual(first.session_id, second.session_id)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.jsonl"
            environment = {"USAGE_LOG": str(path), "BEDROCK_MODEL_ID": "chat-model"}
            with patch.dict(os.environ, environment):
                with track_session(first):
                    record("chat", 1, {"inputTokens": 8, "outputTokens": 2}, False)
                    with self.assertRaises(RuntimeError), track_session(second):
                        record("embed", 1, {"inputTokens": 5}, False, model_id="embed-model")
                        raise RuntimeError("end nested scope")
                    record("chat", 2, {"inputTokens": 12, "outputTokens": 1}, False)
                    record("chat", 1, {"inputTokens": 99}, True)
                record("extract", 1, {}, False)
            with path.open(encoding="utf-8") as log:
                rows = [json.loads(line) for line in log]

        self.assertEqual(
            [row.get("session_id") for row in rows],
            [first.session_id, second.session_id, first.session_id, first.session_id, None],
        )
        self.assertNotIn("session_id", rows[-1])
        self.assertEqual(
            [row["model"] for row in rows],
            ["chat-model", "embed-model", "chat-model", "mock", "chat-model"],
        )
        self.assertEqual((first.input_tokens, first.output_tokens, first.total_tokens), (20, 3, 23))
        self.assertEqual(second.total_tokens, 5)

    def test_session_counts_with_disabled_or_failed_logging(self) -> None:
        session = SessionUsage()
        with track_session(session):
            with patch.dict(os.environ, {"USAGE_LOG": "off"}):
                record("chat", 1, {"inputTokens": 2, "outputTokens": 3}, False)
            with tempfile.TemporaryDirectory() as folder:
                with patch.dict(os.environ, {"USAGE_LOG": folder}), self.assertLogs("ai.usage", "WARNING"):
                    record("chat", 1, {"inputTokens": 7, "outputTokens": 1}, False)
        self.assertEqual((session.input_tokens, session.output_tokens, session.total_tokens), (9, 4, 13))


if __name__ == "__main__":
    unittest.main()
