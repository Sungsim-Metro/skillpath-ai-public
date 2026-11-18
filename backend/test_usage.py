"""토큰·비용 집계 확인. 실행: python -m backend.test_usage"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai.usage as recorder
from ai.bedrock import converse_json
from backend import usage

PRICING = {"source": "테스트", "checked": "2026-11-17", "currency": "USD",
           "models": {"m1": {"input_per_1k": 0.003, "output_per_1k": 0.015}}}


def row(stage="recommend", model="m1", i=1000, o=200, attempt=1, mock_=False, ts="2026-11-17T10:00:00"):
    return {"ts": ts, "stage": stage, "attempt": attempt, "inputTokens": i, "outputTokens": o, "model": model, "mock": mock_}


class UsageTest(unittest.TestCase):
    def test_cost_is_tokens_times_price(self):
        self.assertAlmostEqual(usage.cost_of(row(), PRICING), 1000 / 1000 * 0.003 + 200 / 1000 * 0.015)
        self.assertIsNone(usage.cost_of(row(model="unknown"), PRICING))   # 단가가 없으면 계산하지 않는다

    def test_summary_by_stage_and_day(self):
        rows = [row(), row(attempt=2), row(stage="review", i=500, o=100, ts="2026-11-18T09:00:00"),
                row(model="unknown"), row(mock_=True, i=0, o=0, model="mock")]
        s = usage.summarize(rows, PRICING)
        self.assertEqual(s["total"]["calls"], 4)                 # 가짜 모드 호출은 뺀다
        self.assertEqual(s["mock_calls"], 1)
        self.assertEqual(s["total"]["retries"], 1)
        self.assertEqual(s["total"]["input_tokens"], 3500)
        self.assertEqual(s["total"]["unpriced_calls"], 1)        # 단가 없는 호출은 비용에서 빠지고 건수로 알린다
        self.assertAlmostEqual(s["total"]["cost"], round(2 * 0.006 + 0.003, 4))
        self.assertEqual([r["stage"] for r in s["by_stage"]], ["recommend", "review"])
        self.assertEqual([r["day"] for r in s["by_day"]], ["2026-11-17", "2026-11-18"])
        self.assertEqual(sum(r["calls"] for r in s["by_stage"]), s["total"]["calls"])

    def test_empty_log(self):
        s = usage.summarize([], PRICING)
        self.assertEqual(s["total"]["calls"], 0)
        self.assertIsNone(s["first"])

    def test_sessions_include_only_their_real_calls_and_sort_by_last_call(self):
        earlier, later = "a" * 32, "b" * 32
        rows = [
            {**row(stage="chat_intake"), "session_id": earlier},
            {**row(stage="embedding", i=50, o=0), "session_id": later},
            {**row(stage="agent", ts="2026-11-17T11:00:00", attempt=2), "session_id": later},
            {**row(stage="review", mock_=True), "session_id": earlier},
            row(stage="extract"),
            {**row(), "session_id": "private nickname"},
        ]
        summary = usage.summarize(rows, PRICING)
        self.assertEqual(summary["total"]["calls"], 5)
        self.assertEqual(summary["mock_calls"], 1)
        sessions = summary["by_session"]
        self.assertEqual([s["session_id"] for s in sessions], [later, earlier])
        self.assertEqual(sessions[0]["last"], "2026-11-17T11:00:00")
        self.assertEqual(sessions[0]["calls"], 2)
        self.assertEqual(sessions[0]["retries"], 1)
        self.assertEqual(sessions[0]["input_tokens"], 1050)
        self.assertEqual(sessions[0]["output_tokens"], 200)
        self.assertEqual(sessions[0]["total_tokens"], 1250)
        self.assertEqual(sessions[1]["total_tokens"], 1200)
        self.assertNotIn("private nickname", repr(summary))

    def test_converse_json_records_without_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "u.jsonl"
            with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1", "USAGE_LOG": str(log)}):
                converse_json("비밀 시스템 프롬프트", "비밀 사용자 입력", mock_file="mock_bedrock_recommend.json", mock_key="Docker")
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(set(json.loads(lines[0])), {"ts", "stage", "attempt", "inputTokens", "outputTokens", "model", "mock"})
            self.assertEqual(json.loads(lines[0])["stage"], "recommend")
            self.assertNotIn("비밀", lines[0])
            self.assertEqual(usage.read_rows(log)[0]["mock"], True)
            log.write_text(lines[0] + '\n{끊긴 줄\n{"ts": "2026-11-17T10", "stage": "x", "attempt": 1}\n[1, 2]\n', encoding="utf-8")
            self.assertEqual(len(usage.read_rows(log)), 1)      # 끊긴 줄, 칸이 빠진 줄, 객체가 아닌 줄은 세지 않는다
            usage.summarize(usage.read_rows(log), PRICING)

    def test_record_failure_does_not_break_calls(self):
        with mock.patch.dict(os.environ, {"USAGE_LOG": "/proc/없는/경로/u.jsonl"}):
            recorder.record("mock_bedrock_extract.json", 1, {}, True)   # 오류 없이 지나간다

    def test_shipped_pricing_has_no_guessed_prices(self):
        pricing = usage.load_pricing()
        for model, price in pricing["models"].items():
            self.assertEqual(set(price), {"input_per_1k", "output_per_1k"}, model)
        if pricing["models"]:
            self.assertTrue(pricing["source"] and pricing["checked"])   # 단가를 적었으면 출처와 확인일도 있어야 한다


    def test_partial_pricing_and_odd_rows(self):
        rows = [row(), row(model="unknown", i=900000), {**row(), "ts": "2026-11-16T09:00:00", "attempt": "x", "model": ["m"]}]
        s = usage.summarize(rows, PRICING)
        self.assertEqual(s["unpriced_models"], ["", "unknown"])            # 단가가 있는 모델은 경고 목록에 없다
        self.assertEqual((s["first"], s["last"]), (min(r["ts"] for r in rows), max(r["ts"] for r in rows)))  # 파일 순서가 아니라 시각
        self.assertIsNone(usage.cost_of(row(), {"models": {"m1": {"input_per_1k": 0.003}}}))       # 한쪽 단가만 있으면 계산 안 함
        self.assertFalse(usage._valid({**row(), "inputTokens": -5}))

    def test_record_survives_odd_usage_and_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"USAGE_LOG": str(Path(d) / "u.jsonl")}):
            recorder.record("mock_bedrock_recommend.json", 1, {"inputTokens": None}, False)   # 멈추지 않는다
            recorder.record("mock_bedrock_recommend.json", 1, None, False)
            rows = usage.read_rows(Path(d) / "u.jsonl")
            self.assertEqual((len(rows), rows[0]["inputTokens"]), (1, 0))
            self.assertRegex(rows[0]["ts"], r"^\d{4}-\d\d-\d\dT")
            with mock.patch.dict(os.environ, {"USAGE_LOG": "off"}):
                recorder.record("mock_bedrock_recommend.json", 1, {}, False)
            self.assertFalse(Path("off").exists())
            self.assertEqual(len(usage.read_rows(Path(d) / "u.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
