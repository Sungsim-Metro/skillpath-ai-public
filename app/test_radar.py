"""영역 레이더 화면 확인(가짜 모드, #91). 실행: python -m unittest app.test_radar"""
import os
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

MAIN = str(Path(__file__).resolve().parent / "main.py")


def _radar_caption(at) -> str:
    return next(c.value for c in at.caption if "'마친 뒤'는" in c.value)


def _radar_table(at):
    return next(d.value for d in at.dataframe if "마친 뒤(예상)" in d.value.columns)


class RadarScreenTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_certain_courses_raise_levels(self):
        at = AppTest.from_file(MAIN, default_timeout=60).run()
        self.assertFalse(at.exception)
        note = _radar_caption(at)
        # 가짜 추천: Terraform 은 불확실 과정만, Linux·Python·Git 은 과정 없음(data/sample/recommendations.json)
        self.assertIn("개만 반영했습니다", note)
        self.assertIn("관련 과정만 붙은 기술(Terraform)", note)
        table = _radar_table(at)
        raised = " ".join(table["오르는 기술(단계)"])
        self.assertIn("Docker 0→2", raised)       # 기초 과정 → 2단계
        self.assertIn("Kubernetes 0→3", raised)   # 중급 과정 → 3단계
        self.assertNotIn("Terraform", raised)
        self.assertTrue((table["마친 뒤(예상)"] >= table["지금"]).all())

    def test_recommendation_failure_keeps_after_equal_to_now(self):
        def get_recommendations(*args, **kwargs):  # load() 가 함수 이름을 로그에 남기므로 Mock 이 아닌 함수로 바꾼다
            raise RuntimeError("down")

        with mock.patch("backend.api.get_recommendations", get_recommendations):
            at = AppTest.from_file(MAIN, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertIn("추천을 불러오지 못해 '마친 뒤'가 지금과 같게 나옵니다", _radar_caption(at))
        table = _radar_table(at)
        self.assertTrue((table["마친 뒤(예상)"] == table["지금"]).all())
        self.assertEqual(set(table["오르는 기술(단계)"]), {"-"})


if __name__ == "__main__":
    unittest.main()
