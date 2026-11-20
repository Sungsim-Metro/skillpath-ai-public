"""추천 과정을 마친 뒤의 영역별 Gap(예상) 확인(가짜 모드, #91). 실행: python -m unittest backend.test_area_after"""
import os
import unittest
from unittest import mock

from backend import api
from backend.levels import level_after_courses, required_level, skill_levels

JOB = "cloud_engineer"


def _rec(skill, *levels_and_matches):
    return {"gap_skill": skill, "demand": {}, "courses": [
        {"title": f"{skill}-{i}", "level": lv, "match": m} for i, (lv, m) in enumerate(levels_and_matches)]}


def _demand(total, **postings):
    return {"total_postings": total, "skills": [
        {"skill": name, "postings": n, "required": n, "preferred": 0, "evidence_samples": []}
        for name, n in postings.items()]}


class RepresentativeSkillsTest(unittest.TestCase):
    """Gap 에는 공고의 GAP_MIN_SHARE 이상이 요구한 기술만 들어간다(경계 포함)."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _area_skills(self, demand):
        with mock.patch.object(api, "get_demand", return_value=demand):
            return {s for row in api.get_area_gap(JOB, ()) for s in row["skills"]}

    def test_exactly_the_minimum_share_stays_and_below_it_drops(self):
        demand = _demand(100, AWS=10, Docker=9, Kubernetes=50)  # 10% 는 남고 9% 는 빠진다
        self.assertEqual(api.GAP_MIN_SHARE, 0.10)
        self.assertEqual(self._area_skills(demand), {"AWS", "Kubernetes"})

    def test_no_postings_means_no_skills(self):
        self.assertEqual(self._area_skills(_demand(0, AWS=0)), set())

    def test_gap_list_and_area_gap_use_the_same_skills(self):
        demand = _demand(100, AWS=10, Docker=9, Kubernetes=50)
        with mock.patch.object(api, "get_demand", return_value=demand):
            listed = {r["skill"] for r in api.get_gap(JOB, ())}
        self.assertEqual(listed, self._area_skills(demand))

    def test_representative_does_not_change_its_input(self):
        demand = _demand(100, AWS=10, Docker=9)
        api._representative(demand)
        self.assertEqual([s["skill"] for s in demand["skills"]], ["AWS", "Docker"])


class AreaAfterTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_level_after_courses(self):
        self.assertEqual(level_after_courses(0, ["기초"]), 2)
        self.assertEqual(level_after_courses(0, ["기초", "중급"]), 3)   # 가장 높은 난이도
        self.assertEqual(level_after_courses(4, ["중급"]), 4)           # 지금이 더 높으면 그대로
        self.assertEqual(level_after_courses(1, ["고급?"]), 1)          # 모르는 난이도는 올리지 않는다
        self.assertEqual(level_after_courses(1, []), 1)

    def test_after_uses_course_level_and_skips_uncertain(self):
        levels = {}  # 진단 결과가 비어 있으면 모든 기술 0단계
        before = {r["area_id"]: r for r in api.get_area_gap(JOB, levels)}
        recs = [_rec("Docker", ("기초", "certain")), _rec("Kubernetes", ("중급", "certain")),
                _rec("Terraform", ("중급", "uncertain"))]
        after = {r["area_id"]: r for r in api.get_area_gap_after(JOB, levels, recs)}
        raised = {x["skill"]: (x["from"], x["to"]) for r in after.values() for x in r["raised"]}
        self.assertEqual(raised, {"Docker": (0, 2), "Kubernetes": (0, 3)})  # 불확실 과정만 있는 Terraform 은 그대로
        for area_id, row in after.items():
            self.assertEqual(row["demand"], before[area_id]["demand"])
            self.assertGreaterEqual(row["coverage"], before[area_id]["coverage"])
        # 예상 단계가 기준 단계에 닿은 기술만 부족 목록에서 빠진다. 기준 단계는 파일에서 읽는다(재생성돼도 맞게)
        compute = next(r for r in after.values() if "Docker" in r["skills"])
        for skill, to in (("Docker", 2), ("Kubernetes", 3)):
            self.assertEqual(skill in compute["missing_skills"], to < required_level(skill)[0], skill)
        self.assertGreater(compute["coverage"], before[compute["area_id"]]["coverage"])

    def test_no_courses_or_higher_level_changes_nothing(self):
        levels = {"C1-2": 4}
        now = api.get_area_gap(JOB, levels)
        self.assertEqual([{k: v for k, v in r.items() if k != "raised"} for r in api.get_area_gap_after(JOB, levels, [])], now)
        linux_level = skill_levels(levels, ["Linux"])["Linux"]
        self.assertGreaterEqual(linux_level, 3)
        after = api.get_area_gap_after(JOB, levels, [_rec("Linux", ("중급", "certain"))])
        self.assertFalse([x for r in after for x in r["raised"]])  # 이미 4단계면 중급 과정으로 오르지 않는다

    def test_ai_engineer_uses_its_own_areas_and_courses(self):
        levels = {"A1-1": 2}
        rows = {r["area_id"]: r for r in api.get_area_gap("ai_engineer", levels)}
        self.assertTrue({"A1", "A2", "A3", "A4"} <= set(rows))
        self.assertEqual(rows["UNCLASSIFIED"]["demand"], 0)  # 가짜 AI 수요의 기술은 모두 영역에 들어간다
        recs = [_rec("RAG", ("중급", "certain")), _rec("Docker", ("기초", "certain"))]
        raised = {x["skill"]: x["to"] for r in api.get_area_gap_after("ai_engineer", levels, recs) for x in r["raised"]}
        self.assertEqual(raised, {"RAG": 3, "Docker": 2})


if __name__ == "__main__":
    unittest.main()
