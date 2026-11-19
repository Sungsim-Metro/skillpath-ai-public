"""진단 보고서(.md)와 수강 순서 확인(가짜 모드). 실행: python -m app.test_report"""
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from app.testing import open_app, set_job

MAIN = str(Path(__file__).resolve().parent / "main.py")


def _course(title, match="certain", level="기초", prerequisites="없음", url=None):
    return {"title": title, "url": url or f"https://example.com/{title}", "level": level, "prerequisites": prerequisites,
            "reason": "이유", "match": match}


def _rec(skill, *courses):
    return {"gap_skill": skill, "demand": {"of": 40, "postings": 10, "required_ratio": 0.5}, "courses": list(courses)}


class StudyOrderTest(unittest.TestCase):
    def test_prerequisite_skill_comes_first_and_uncertain_is_left_out(self):
        from backend.plan import study_order, uncovered_skills

        recs = [_rec("Kubernetes", _course("EKS", level="중급", prerequisites="Docker 기초")),
                _rec("Docker", _course("ECS")),
                _rec("Terraform", _course("IaC", match="uncertain")),
                _rec("Go", _course("ECS")),  # 같은 과정이 또 추천되면 한 번만
                _rec("Rust")]
        plan = study_order(recs)
        self.assertEqual([p["title"] for p in plan], ["ECS", "EKS"])
        self.assertEqual(plan[1]["after"], ["Docker"])
        self.assertEqual([p["step"] for p in plan], [1, 2])
        self.assertEqual(uncovered_skills(recs), [{"skill": "Terraform", "status": "uncertain"},
                                                  {"skill": "Rust", "status": "none"}])

    def test_skill_name_must_be_a_whole_word(self):
        from backend.plan import study_order

        recs = [_rec("AWS", _course("A", prerequisites="Google 계정")), _rec("Go", _course("B"))]
        self.assertEqual([p["title"] for p in study_order(recs)], ["A", "B"])

    def test_mutual_prerequisites_keep_original_order(self):
        from backend.plan import study_order

        recs = [_rec("X", _course("A", prerequisites="Y")), _rec("Y", _course("B", prerequisites="X"))]
        self.assertEqual([p["title"] for p in study_order(recs)], ["A", "B"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, owned, **kw):
        from app.report import build_report
        from backend import api
        from backend.plan import study_order, uncovered_skills

        demand, gap = api.get_demand("cloud_engineer"), api.get_gap("cloud_engineer", owned)
        missing = [r for r in gap if not r["owned"]]
        recs = kw.pop("recs", None) or api.get_recommendations("cloud_engineer", [r["skill"] for r in missing])
        args = dict(job_title="클라우드 엔지니어", demand=demand, gap_rows=gap,
                    area_rows=api.get_area_gap("cloud_engineer", owned), recommendations=recs, plan=study_order(recs),
                    uncovered=uncovered_skills(recs), market_rows=api.get_market_gap("cloud_engineer")["rows"],
                    mock=True, today=date(2026, 11, 19))
        args.update(kw)
        return build_report(**args), demand, gap, missing, recs

    def test_report_matches_screen(self):
        """보고서의 숫자는 화면이 받은 값 그대로다."""
        from app.report import file_name

        md, demand, gap, missing, recs = self._build(["Python", "Linux", "Git"])
        self.assertIn(f"**{len(missing)}개가 부족**", md)
        self.assertIn(f"채용공고 {demand['total_postings']}건", md)
        for r in gap:
            self.assertIn(f"{r['postings']} / {r['of']}", md)
        for rec in recs:
            for c in rec["courses"]:
                self.assertIn(c["url"], md)
        for heading in ("## 1. 역량 진단", "## 2. 학습 프로그램 추천", "## 3. 수강 순서", "## 4. 과정으로 채우기 어려운 기술"):
            self.assertIn(heading, md)
        self.assertIn("가짜 데이터 모드", md)
        self.assertEqual(file_name(date(2026, 11, 19)), "skillpath-report-20261119.md")

    def test_data_text_is_not_markup(self):
        """데이터 글자는 마크다운·HTML 로 해석되지 않고, http(s) 가 아닌 링크는 걸지 않는다."""
        recs = [_rec("Docker", _course("<script>alert(1)</script> | **굵게**", url="javascript:alert(1)"),
                     _course("괄호", url="https://example.com/a b)c"))]
        md, *_ = self._build(["Python"], recs=recs, job_title="<b>x</b>")
        self.assertNotIn("<script>", md)
        self.assertNotIn("<b>x</b>", md)
        self.assertNotIn("javascript:", md)
        self.assertIn(r"\| \*\*굵게\*\*", md)
        self.assertIn("(https://example.com/a%20b%29c)", md)

    def test_report_says_why_recommendations_are_missing_or_partial(self):
        md, *_ = self._build(["Python"], recs=[_rec("X")], recommendations=[], plan=[], uncovered=[], recommend_failed=True)
        self.assertIn("추천을 불러오지 못해", md)
        self.assertNotIn("부족한 기술이 없어", md)
        partial, _, _, missing, _ = self._build(["Python"], recs=[_rec("AWS", _course("A"))])
        self.assertIn("이번에 추천하지 않은 기술:", partial)
        self.assertIn(missing[1]["skill"], partial.split("이번에 추천하지 않은 기술:")[1])

    def test_report_screen_builds_the_report_only_after_diagnosis(self):
        """메뉴의 진단 보고서 화면: 진단 전(예시 값)에는 안내만 하고, 진단 뒤에는 화면에서 보고 내려받는다."""
        at = open_app("report")
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "진단 보고서")
        self.assertTrue(any("수준 진단" in i.value for i in at.info))
        self.assertFalse(at.get("download_button"))
        self.assertFalse([b for b in at.button if b.key == "open_report"])  # 사이드바 버튼은 없앴다
        at.session_state["levels_example"] = False
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue([b for b in at.get("download_button") if b.key == "report_btn"])
        self.assertTrue(any("역량 진단 보고서" in m.value for m in at.markdown))

    def test_switching_target_role_changes_both_the_dashboard_and_report(self):
        def market(job):
            demand_pct = {"cloud_engineer": 60., "ai_engineer": 70.}[job]
            row = {"job_category": job, "skill": "Python", "demand_pct": demand_pct,
                   "supply_pct": 10., "gap_pp": demand_pct - 10.}
            return {"rows": [row], "source": "검산 자료"}

        with mock.patch("backend.api.get_market_gap", side_effect=market):
            at = open_app("gap")
            self.assertFalse(at.exception)
            table = next(frame.value for frame in at.dataframe if "Gap" in frame.value.columns)
            self.assertEqual(table["Gap"].tolist(), [50.])
            at.session_state["levels"] = {}
            at.session_state["levels_example"] = False
            set_job(at, "ai_engineer")
            table = next(frame.value for frame in at.dataframe if "Gap" in frame.value.columns)
            self.assertEqual(table["직군"].tolist(), ["AI 엔지니어"])
            self.assertEqual(table["Gap"].tolist(), [60.])
            at.radio(key="view").set_value("report").run()
        self.assertFalse(at.exception)
        md = next(item.value for item in at.markdown if "시장 Gap (내 부족 기술)" in item.value)
        self.assertIn("AI 엔지니어", md)
        self.assertIn("| Python | 70.0% | 10.0% | +60.0%p |", md)
        self.assertNotIn("| Python | 60.0% | 10.0% | +50.0%p |", md)


if __name__ == "__main__":
    unittest.main()
