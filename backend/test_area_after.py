"""추천 과정을 마친 뒤의 영역별 Gap(예상) 확인(가짜 모드, #91). 실행: python -m unittest backend.test_area_after"""
import os
import unittest
from unittest import mock

from backend import api
from backend.levels import level_after_courses, skill_levels

JOB = "cloud_engineer"


def _rec(skill, *levels_and_matches):
    return {"gap_skill": skill, "demand": {}, "courses": [
        {"title": f"{skill}-{i}", "level": lv, "match": m} for i, (lv, m) in enumerate(levels_and_matches)]}


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
        # 중급(3단계)은 기준 단계(추정 3)에 닿아 부족 목록에서 빠지고, 기초(2단계)는 남는다
        compute = next(r for r in after.values() if "Docker" in r["skills"])
        self.assertIn("Docker", compute["missing_skills"])
        self.assertNotIn("Kubernetes", compute["missing_skills"])
        self.assertGreater(compute["coverage"], before[compute["area_id"]]["coverage"])

    def test_no_courses_or_higher_level_changes_nothing(self):
        levels = {"C1-2": 4}
        now = api.get_area_gap(JOB, levels)
        self.assertEqual([{k: v for k, v in r.items() if k != "raised"} for r in api.get_area_gap_after(JOB, levels, [])], now)
        linux_level = skill_levels(levels, ["Linux"])["Linux"]
        self.assertGreaterEqual(linux_level, 3)
        after = api.get_area_gap_after(JOB, levels, [_rec("Linux", ("중급", "certain"))])
        self.assertFalse([x for r in after for x in r["raised"]])  # 이미 4단계면 중급 과정으로 오르지 않는다


if __name__ == "__main__":
    unittest.main()
