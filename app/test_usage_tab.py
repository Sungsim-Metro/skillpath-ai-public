"""관리자 화면의 토큰·비용 탭 확인(가짜 모드). 실행: python -m app.test_usage_tab

여는 조건(주소의 admin 값)은 app/test_admin_page.py 가 확인한다.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
PAGE = f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nfrom app import admin\nadmin.render()\n"


def usage_tab(records: list[dict]):
    """기록을 넣고 관리자 화면을 그린 뒤 토큰·비용 탭을 돌려준다."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "usage.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1", "USAGE_LOG": str(path), "PYTHON_DOTENV_DISABLED": "1",
                                          "FEEDBACK_LOG": str(Path(directory) / "feedback.jsonl")}):
            at = AppTest.from_string(PAGE, default_timeout=60).run()
    assert not at.exception, at.exception
    tab = next(tab for tab in at.tabs if tab.label == "토큰·비용")
    return tab


class UsageTabTest(unittest.TestCase):
    def test_usage_tab_counts_calls_and_flags_unpriced_models(self):
        tab = usage_tab([{"ts": "2026-11-17T10:00:00", "stage": "recommend", "attempt": 1, "inputTokens": 1200,
                          "outputTokens": 300, "model": "some-model", "mock": False}])
        self.assertIn("1번", [m.value for m in tab.metric])
        self.assertIn("1,200", [m.value for m in tab.metric])
        self.assertTrue(any("단가가 없는 모델" in w.value for w in tab.warning))
        self.assertTrue(any("세션 ID가 기록된 실제 호출이 없습니다" in t.value for t in tab.caption))

    def test_usage_tab_shows_session_and_model_totals_and_distinguishes_batch_calls(self):
        first, second = "a" * 32, "b" * 32
        base = {"ts": "2026-11-19T10:00:00", "stage": "chat_intake", "attempt": 1,
                "inputTokens": 100, "outputTokens": 10, "model": "test", "mock": False}
        tab = usage_tab([
            {**base, "session_id": first},
            {**base, "session_id": second, "ts": "2026-11-19T11:00:00"},
            {**base, "stage": "embedding", "session_id": second, "inputTokens": 20, "outputTokens": 0, "model": "embed"},
            {**base, "stage": "extract"},
        ])
        self.assertIn("4번", [metric.value for metric in tab.metric])
        sessions = next(frame.value for frame in tab.dataframe if "세션 ID" in frame.value)
        self.assertEqual(sessions["세션 ID"].tolist(), [second, first])
        self.assertEqual(sessions["합계 토큰"].tolist(), [130, 110])
        self.assertEqual(sessions["호출"].tolist(), [2, 1])
        models = next(frame.value for frame in tab.dataframe if "모델" in frame.value)
        self.assertEqual(dict(zip(models["모델"], models["호출"])), {"embed": 1, "test": 3})


if __name__ == "__main__":
    unittest.main()
