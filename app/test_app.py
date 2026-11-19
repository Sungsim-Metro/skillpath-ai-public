"""화면이 가짜 모드에서 오류 없이 뜨는지 확인한다. 화면 내용의 옳고 그름은 보지 않는다.

실행: 저장소 루트에서 `python -m app.test_app`
"""
import json
import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from app.testing import MENU, open_app


class AppSmokeTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.at = open_app("gap")

    def test_every_menu_screen_renders_in_mock_mode(self):
        first = open_app()
        self.assertEqual(first.session_state["view"], "chat")  # 첫 화면은 학습 상담
        self.assertEqual(list(first.sidebar.radio(key="view").options), list(MENU.values()))
        self.assertEqual(open_app("dashboard").session_state["view"], "chat")  # 모르는 값은 첫 화면으로
        for view, label in MENU.items():
            at = open_app(view)
            self.assertFalse(at.exception, view)
            self.assertEqual(bool(at.tabs), view == "recommend", view)  # 학습 추천만 기술별 탭이 있다
            self.assertEqual(at.title[0].value, label)

    def test_recommend_screen_shows_study_order_ranked_cards_and_ratings(self):
        """학습 추천 화면: 먼저 들을 순서, 기술마다 1위부터 붙는 카드, 평점 줄(있는 과정만)."""
        rated = {"title": "평점 있는 과정", "url": "https://example.com/a", "level": "기초", "prerequisites": "없음",
                 "reason": "예시 이유", "match": "certain", "rating_avg": 4.6, "rating_count": 1280}
        plain = {**{k: v for k, v in rated.items() if not k.startswith("rating_")}, "title": "평점 없는 과정",
                 "url": "https://example.com/b", "match": "uncertain"}

        def get_recommendations(job, skills, **kwargs):  # load() 가 함수 이름을 로그에 남기므로 Mock 이 아닌 함수로 바꾼다
            demand = {"postings": 20, "of": 40, "required_ratio": 0.5}
            return [{"gap_skill": skills[0], "demand": demand, "courses": [rated, plain]},
                    {"gap_skill": skills[1], "demand": demand, "courses": [rated]}]

        with mock.patch("backend.api.get_recommendations", get_recommendations):
            at = open_app("recommend")
        self.assertFalse(at.exception)
        self.assertEqual([tab.label for tab in at.tabs][:2], [f"1. {at.subheader[1].value}", f"2. {at.subheader[2].value}"])
        self.assertIn("먼저 들을 순서", [h.value for h in at.subheader])
        self.assertTrue(any(t.value.startswith("1. 평점 있는 과정") for t in at.text))  # 불확실 과정은 순서에 넣지 않는다
        self.assertFalse(any("평점 없는 과정 (" in t.value for t in at.text))
        badges = [m.value.split("]")[0] for m in at.markdown if m.value.startswith(":blue-badge[")]
        self.assertEqual(badges, [":blue-badge[1위", ":blue-badge[2위", ":blue-badge[1위"])
        ratings = [b for b in at.get("link_button") if b.proto.label.startswith("★")]
        self.assertEqual([b.proto.label for b in ratings], ["★ 4.6 · 평가 1,280개"] * 2)  # 평점을 누르면 과정 페이지가 열린다
        self.assertEqual({b.proto.url for b in ratings}, {"https://example.com/a"})
        marks = " ".join(m.value for m in at.markdown)
        self.assertIn("AWS Skill Builder", marks)   # 직접 다루는 과정
        self.assertIn("유사 과정", marks)            # 직접 다루는지 확인하지 못한 과정
        self.assertNotIn("불확실", marks)
        self.assertEqual([box.label for box in at.main.expander], ["추천 이유 더보기"] * 3)  # 추천 이유는 카드마다 접어 둔다

    def test_extract_button_shows_result(self):
        """공고 직접 분석은 메뉴에서 뺐지만 화면 코드는 남아 있다."""
        at = AppTest.from_function(_extract_screen).run()
        self.assertFalse(at.exception)
        at = at.button(key="extract_btn").click().run()
        self.assertFalse(at.exception)
        self.assertIn("추출 결과", [h.value for h in at.subheader])

    def test_level_dialog_requires_every_answer(self):
        from backend.levels import job_subskills

        subskills = list(job_subskills("cloud_engineer"))  # 기본 직무(클라우드)의 질문만 나온다

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


def _extract_screen():
    from app.common import build_ctx
    from app.tabs.extract import render

    render(build_ctx("cloud_engineer", {}, None))


def _market_without_personal_demand():
    from app.common import Ctx
    from app.tabs.gap import render

    render(Ctx(job="cloud_engineer", demand=None, demand_err="수요 미연결", owned=[], levels={},
               skill_options=[], gap_rows=None, gap_err="수요 미연결"))


class MarketGapScreenTest(unittest.TestCase):
    def test_supply_counts_are_visible_without_postings(self):
        supply = {"source": "교육과정 목록", "total_courses": 3,
                  "skills": [{"skill": "Docker", "courses": 1}, {"skill": "SQL", "courses": 2}]}
        with mock.patch("backend.api.get_supply", return_value=supply), \
             mock.patch("backend.api.get_market_gap", side_effect=FileNotFoundError):
            at = AppTest.from_function(_market_without_personal_demand).run()
        self.assertFalse(at.exception)
        self.assertIn("교육 공급", [box.label for box in at.expander])
        self.assertTrue(any("교육과정 목록" in caption.value and "3개" in caption.value for caption in at.caption))
        table = next(frame.value for frame in at.dataframe if "과정 수" in frame.value.columns)
        self.assertEqual(table.to_dict("records"), [{"기술": "SQL", "과정 수": 2}, {"기술": "Docker", "과정 수": 1}])

    def test_market_gap_shows_owned_skills_without_changing_market_values(self):
        rows = [{"job_category": "cloud_engineer", "skill": "AWS", "demand_pct": 60.0, "supply_pct": 40.0, "gap_pp": 20.0},
                {"job_category": "cloud_engineer", "skill": "SQL", "demand_pct": 40.0, "supply_pct": 60.0, "gap_pp": -20.0}]
        with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), \
             mock.patch("backend.api.owned_skill_names", return_value=["AWS"]), \
             mock.patch("backend.api.get_market_gap", return_value={"rows": rows, "source": "검산 자료"}):
            at = open_app("gap")
        self.assertFalse(at.exception)
        table = next(frame.value for frame in at.dataframe if "Gap" in frame.value.columns)
        self.assertEqual(table["내 보유"].tolist(), ["보유", "-"])
        self.assertEqual(table["Gap"].tolist(), [20.0, -20.0])

    def test_empty_personal_demand_keeps_job_market_analysis_available(self):
        demand = {"total_postings": 0, "skills": []}
        rows = [{"job_category": "cloud_engineer", "skill": "AWS", "demand_pct": 100.0, "supply_pct": 100.0, "gap_pp": 0.0}]
        with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), \
             mock.patch("backend.api.get_demand", return_value=demand), \
             mock.patch("backend.api.get_market_gap", return_value={"rows": rows, "source": "예시 입력"}):
            at = open_app("gap")
        self.assertFalse(at.exception)
        table = next(frame.value for frame in at.dataframe if "Gap" in frame.value.columns)
        self.assertEqual(table["직군"].tolist(), ["클라우드 엔지니어"])

    def test_job_charts_and_download_are_available_without_personal_demand(self):
        import streamlit as st

        rows = [
            {"job_category": "cloud_engineer", "skill": "AWS", "demand_pct": 60., "supply_pct": 80., "gap_pp": -20.},
            {"job_category": "cloud_engineer", "skill": "SQL", "demand_pct": 40., "supply_pct": 20., "gap_pp": 20.},
        ]
        with mock.patch("backend.api.get_market_gap", return_value={"rows": rows, "source": "검산 자료"}) as market, \
             mock.patch.object(st, "download_button", wraps=st.download_button) as download:
            at = AppTest.from_function(_market_without_personal_demand).run()
        self.assertFalse(at.exception)
        market.assert_called_with("cloud_engineer")
        self.assertFalse(at.selectbox)
        charts = [json.loads(c.proto.spec) for c in at.get("vega_lite_chart")]
        self.assertEqual(charts[0]["encoding"]["y"]["sort"], ["AWS", "SQL"])
        self.assertEqual(charts[1]["encoding"]["y"]["sort"], ["SQL", "AWS"])
        self.assertIn("cloud_engineer", download.call_args.kwargs["file_name"])
        csv = download.call_args.kwargs["data"].decode("utf-8-sig").splitlines()
        self.assertEqual(csv, ["job_category,skill,demand_pct,supply_pct,gap_pp",
                               "cloud_engineer,AWS,60.0,80.0,-20.0", "cloud_engineer,SQL,40.0,20.0,20.0"])

    def test_empty_table_and_missing_files_explain_absence(self):
        with mock.patch("backend.api.get_market_gap", return_value={"rows": [], "source": "예시 입력"}):
            at = AppTest.from_function(_market_without_personal_demand).run()
            self.assertFalse(at.exception)
            self.assertIn("매칭된 기술", at.info[-1].value)
            self.assertFalse(at.selectbox)
        with mock.patch("backend.api.get_market_gap", side_effect=FileNotFoundError):
            at = AppTest.from_function(_market_without_personal_demand).run()
            self.assertFalse(at.exception)
            self.assertIn("data/courses/courses.csv", at.info[-1].value)
            self.assertIn("클라우드 엔지니어", at.info[-1].value)


if __name__ == "__main__":
    unittest.main()
