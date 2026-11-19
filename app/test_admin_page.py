"""관리자 화면(가짜 모드). 실행: python -m unittest app.test_admin_page

주소 /admin 으로 들어가는 길은 화면 테스트 도구가 따라가지 못해, 여는 조건(allowed)과 화면 그리기(render)를 따로 확인한다.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app import admin
from backend import experiments

ROOT = Path(__file__).resolve().parent.parent
MAIN = str(Path(__file__).with_name("main.py"))
PAGE = f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nfrom app import admin\nadmin.render()\n"


class AdminPageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "PYTHON_DOTENV_DISABLED": "1", "ADMIN_KEY": "열쇠-1",
            "USAGE_LOG": str(self.folder / "usage.jsonl"), "FEEDBACK_LOG": str(self.folder / "feedback.jsonl"),
        }))

    def test_only_the_exact_key_opens_it(self):
        self.assertTrue(admin.allowed("열쇠-1"))
        for wrong in ("", None, "1", "열쇠", "열쇠-12"):
            self.assertFalse(admin.allowed(wrong), wrong)
        with patch.dict(os.environ, {"ADMIN_KEY": ""}):
            self.assertFalse(admin.allowed(""))                # 열쇠를 정하지 않았으면 열리지 않는다

    def test_user_screen_is_unchanged_and_has_no_admin_tab(self):
        at = AppTest.from_file(MAIN, default_timeout=60)
        at.query_params["admin"] = "열쇠-1"
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual([tab.label for tab in at.tabs], ["시장 수요", "부족 역량", "학습 추천", "공고 직접 분석"])

    def test_every_section_renders_without_data(self):
        at = AppTest.from_string(PAGE, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertEqual([tab.label for tab in at.tabs], ["비교 실험", "토큰·비용", "데이터 현황", "과정 커버리지", "평가 결과"])
        self.assertTrue(any("아직 기록된 턴이 없습니다" in info.value for info in at.info))

    def test_model_comparison_and_saved_decision_are_shown(self):
        turn = {"job": "cloud_engineer", "action": "gap", "turn_tokens": {"input": 1000, "output": 200}}
        models = {"global.anthropic.claude-sonnet-4-6": (18, 2), "global.anthropic.claude-haiku-4-5-20251001-v1:0": (17, 3)}
        number = 0
        for model, (ups, downs) in models.items():
            for rating in ["up"] * ups + ["down"] * downs:
                number += 1
                experiments.save_feedback("s", f"t{number}", model=model, rating=rating, **turn)
        experiments.save_feedback("s", "ab", left="single", right="reviewed", picked="reviewed",
                                  tokens={"single": {"input": 100, "output": 10}, "reviewed": {"input": 300, "output": 30}})
        (self.folder / "usage.jsonl").write_text(json.dumps({
            "ts": "2026-11-20T12:00:00", "stage": "chat_intake", "attempt": 1, "inputTokens": 10, "outputTokens": 5,
            "model": "global.anthropic.claude-sonnet-4-6", "mock": False}) + "\n", encoding="utf-8")
        at = AppTest.from_string(PAGE, default_timeout=60).run()
        self.assertFalse(at.exception)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("쓸 모델: global.anthropic.claude-haiku-4-5-20251001-v1:0", text)   # 5%p 안이면 싼 모델
        frames = [frame.value for frame in at.dataframe]
        self.assertTrue(any("좋아요 비율" in frame.columns and list(frame["좋아요 비율"]) == ["85%", "90%"] for frame in frames))
        self.assertTrue(any("선택률" in frame.columns for frame in frames))
        self.assertTrue(any("모델" in frame.columns and "호출" in frame.columns for frame in frames))  # 토큰·비용의 모델별 표
        at.button(key="save_decision").click().run()
        self.assertFalse(at.exception)
        saved = experiments.load_decisions()
        self.assertEqual(saved[0]["decision"]["model"], "global.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertEqual(len(saved[0]["models"]), 2)


if __name__ == "__main__":
    unittest.main()
