"""목표 직무를 AI 엔지니어로 골랐을 때의 화면 확인(가짜 모드, 2026-11-19). 실행: python -m unittest app.test_ai_job"""
import os
import unittest
from unittest import mock

from app.testing import open_app


class AiJobScreenTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.at = open_app("gap")
        self.at.sidebar.selectbox(key="job").set_value("ai_engineer").run()

    def test_dashboard_uses_ai_data(self):
        at = self.at
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "부족 역량")
        self.assertTrue(any(c.value.startswith("AI 엔지니어 · 채용공고") for c in at.caption))
        captions = " ".join(c.value for c in at.sidebar.caption)
        self.assertIn("생성형 AI·프롬프트", captions)      # 사이드바 영역 평균이 AI 영역
        self.assertNotIn("컴퓨팅·서버", captions)
        self.assertTrue(any("'마친 뒤'는" in c.value for c in at.caption))  # 레이더가 그려진다(AI 영역 4개)

    def test_level_dialog_asks_ai_questions_and_keeps_cloud_answers(self):
        from backend.levels import job_subskills

        at = self.at
        at.session_state["levels_example"] = False
        at.session_state["levels"] = {"C1-4": 4}  # 클라우드에서 이미 진단한 값
        at.sidebar.button(key="open_levels").click().run()
        ai = list(job_subskills("ai_engineer"))
        self.assertTrue(at.get("progress")[0].proto.text.startswith(f"0 / {len(ai)}개 응답"))
        for sid in ai:
            at.session_state[f"lv_{sid}"] = 0
        at.session_state["lv_page"] = 3
        at = at.sidebar.button(key="open_levels").click().run()  # 마지막 영역(A4) 페이지를 연다
        at.sidebar.button(key="open_levels").click()
        at.button(key="lvb_A4-3_2").click()  # AppTest 는 모달 안 클릭 뒤 모달을 다시 그리지 않아 열기도 함께 누른다
        at = at.run()
        at.sidebar.button(key="open_levels").click()
        at.button(key="save_levels").click()
        at = at.run()
        self.assertFalse(at.exception)
        saved = at.session_state["levels"]
        self.assertEqual(saved["C1-4"], 4)   # 다른 직무의 진단은 그대로
        self.assertEqual(saved["A4-3"], 2)

    def test_report_names_the_ai_job(self):
        at = self.at
        at.session_state["levels_example"] = False
        at.run()
        at.sidebar.button(key="open_report").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("# AI 엔지니어 역량 진단 보고서" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
