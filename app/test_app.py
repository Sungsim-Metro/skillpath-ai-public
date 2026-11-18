"""화면이 가짜 모드에서 오류 없이 뜨는지 확인한다. 화면 내용의 옳고 그름은 보지 않는다.

실행: 저장소 루트에서 `python -m app.test_app`
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

MAIN = str(Path(__file__).resolve().parent / "main.py")


class AppSmokeTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.at = AppTest.from_file(MAIN, default_timeout=60).run()

    def test_all_tabs_render_in_mock_mode(self):
        self.assertFalse(self.at.exception)
        self.assertEqual([t.label for t in self.at.tabs], ["시장 수요", "부족 역량", "학습 추천", "공고 직접 분석"])

    def test_extract_button_shows_result(self):
        at = self.at.button(key="extract_btn").click().run()
        self.assertFalse(at.exception)
        self.assertIn("추출 결과", [h.value for h in at.subheader])

    def test_level_dialog_requires_every_answer(self):
        from backend.levels import level_guide

        subskills = list(level_guide()["subskills"])

        def reopen(at, button=None):
            # AppTest 는 모달 안 클릭 뒤 모달을 다시 그리지 않아, 같은 실행에서 모달 열기도 누른다
            at.sidebar.button(key="open_levels").click()
            if button:
                at.button(key=button).click()
            return at.run()

        at = reopen(self.at)
        self.assertTrue(at.get("progress")[0].proto.text.startswith(f"0 / {len(subskills)}개 응답 · 남은 시간 약"))
        self.assertNotIn("save_levels", [b.key for b in at.button])  # 저장은 마지막 영역에만
        at = reopen(at, "lvb_C1-4_4")  # 카드를 누르면 그 단계를 고른다
        self.assertEqual(at.session_state["lv_C1-4"], 4)
        at = reopen(at, "lvnav_next")
        self.assertIn("2/8", at.subheader[-1].value)
        for sid in subskills:
            if sid != "C1-4":
                at.session_state[f"lv_{sid}"] = None if sid == "C8-3" else 0
        at.session_state["lv_page"] = 7
        at = reopen(at)
        self.assertTrue(at.button(key="save_levels").disabled)
        at = reopen(at, "lvb_C8-3_0")
        self.assertFalse(at.button(key="save_levels").disabled)
        at = reopen(at, "save_levels")
        self.assertFalse(at.exception)
        self.assertEqual({k: v for k, v in at.session_state["levels"].items() if v}, {"C1-4": 4})
        self.assertFalse(at.session_state["levels_example"])
        self.assertNotIn("lv_page", at.session_state)

    def test_renders_without_owned_skills(self):
        self.at.session_state["levels"] = {}
        self.at.session_state["levels_example"] = False
        at = self.at.run()
        self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
