"""직무별 데이터와 수준 계산 확인(가짜 모드, 2026-11-19 AI 엔지니어). 실행: python -m unittest backend.test_jobs"""
import os
import unittest
from unittest import mock

from backend import api
from backend.jobs import JOBS, job_file
from backend.levels import job_subskills, level_guide, skill_levels, subskills_for, validate_levels


class JobsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_job_file(self):
        self.assertEqual(job_file("skill_areas.json"), "skill_areas.json")
        self.assertEqual(job_file("skill_areas.json", "ai_engineer"), "skill_areas_ai_engineer.json")
        with self.assertRaises(ValueError):
            job_file("skill_areas.json", "backend")

    def test_every_job_has_its_own_data(self):
        for job in JOBS:
            with self.subTest(job=job):
                demand = api.get_demand(job)
                self.assertEqual(demand["job_category"], job)
                areas = api.get_areas(job)
                subskills = job_subskills(job)
                self.assertTrue(subskills)
                self.assertTrue({row["area_id"] for row in subskills.values()} <= set(areas))  # 질문마다 영역이 있다
                for row in subskills.values():
                    self.assertEqual(set(row["levels"]), {"1", "2", "3", "4", "5"})

    def test_subskill_ids_do_not_overlap(self):
        cloud, ai = set(job_subskills("cloud_engineer")), set(job_subskills("ai_engineer"))
        self.assertFalse(cloud & ai)
        self.assertEqual(set(level_guide()["subskills"]), cloud | ai)
        validate_levels({"C5-2": 3, "A1-1": 2})  # 두 직무의 진단을 한 번에 저장할 수 있다

    def test_shared_skill_uses_both_diagnoses(self):
        self.assertIn("A4-2", subskills_for("Docker", "ai_engineer"))
        self.assertTrue(set(subskills_for("Docker", "cloud_engineer")) <= set(subskills_for("Docker", "ai_engineer")))
        cloud_docker = subskills_for("Docker", "cloud_engineer")[0]
        self.assertEqual(skill_levels({cloud_docker: 4}, ["Docker"], "ai_engineer")["Docker"], 4)
        self.assertEqual(skill_levels({"A4-2": 2}, ["Docker"], "cloud_engineer")["Docker"], 0)  # 클라우드는 AI 진단을 안 본다

    def test_common_skill_averages_only_this_jobs_diagnosis(self):
        levels = {"C5-2": 5, "A1-1": 1}
        self.assertEqual(skill_levels(levels, ["AWS"], "cloud_engineer")["AWS"], 5)
        self.assertEqual(skill_levels(levels, ["AWS"], "ai_engineer")["AWS"], 1)

    def test_mock_recommendations_follow_the_job(self):
        recs = api.get_recommendations("ai_engineer", ["LLM", "SQL"])
        self.assertEqual([r["gap_skill"] for r in recs], ["LLM", "SQL"])
        self.assertTrue(recs[0]["courses"])
        self.assertEqual(recs[1]["courses"], [])


if __name__ == "__main__":
    unittest.main()
