"""현직자 연결 카드 화면 확인(가짜 모드). 실행: python -m app.test_connect"""
import os
import unittest
from unittest import mock

from app.testing import open_app


class ConnectScreenTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.at = open_app("connect")

    def _section_text(self, at) -> str:
        self.assertFalse(at.exception)
        self.assertTrue(any(s.value == "현직자에게 물어보기" for s in at.subheader))
        return " ".join(m.value for m in at.markdown)

    def test_section_shows_for_both_jobs(self):
        self.assertIn("부족한 영역을 다루는 개발자 모임", self._section_text(self.at))
        at = self.at.sidebar.selectbox(key="job").set_value("ai_engineer").run()
        text = self._section_text(at)
        self.assertIn("LinkedIn에서 직접 찾아보기", text)
        self.assertNotIn("컨테이너 #container 소모임** · 서울(오프라인) · 부족 영역: 컴퓨팅", text)  # 클라우드 영역 이름이 섞이지 않는다


if __name__ == "__main__":
    unittest.main()
