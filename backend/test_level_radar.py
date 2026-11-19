"""마이페이지 레이더 차트의 값 확인(2026-11-20). 실행: python -m unittest backend.test_level_radar"""
import os
import unittest
from unittest import mock

from backend import api
from backend.levels import DEFAULT_REQUIRED_LEVEL


class LevelRadarTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_follow_the_area_table_without_reading_demand(self):
        for job in ("cloud_engineer", "ai_engineer"):
            with mock.patch.object(api, "get_demand", side_effect=AssertionError("수요 집계를 부르면 안 된다")):
                rows = api.get_level_radar(job, {})
            areas = api.get_areas(job)
            self.assertTrue(rows)
            self.assertTrue({r["area_id"] for r in rows} <= set(areas))
            self.assertNotIn("COMMON", [r["area_id"] for r in rows])
            for row in rows:
                self.assertEqual(row["area"], areas[row["area_id"]])
                self.assertTrue(row["skills"])
                self.assertEqual(row["level"], 0)  # 진단한 것이 없으면 0단계
                self.assertTrue(0 < row["required"] <= 5)
                self.assertTrue(0 <= row["required_uncertain"] <= len(row["skills"]))

    def test_levels_raise_only_the_areas_they_belong_to_and_defaults_are_counted(self):
        empty = {r["area_id"]: r for r in api.get_level_radar("cloud_engineer", {})}
        rated = {r["area_id"]: r for r in api.get_level_radar("cloud_engineer", {"C1-4": 4})}  # 컨테이너 4단계
        raised = [area for area in rated if rated[area]["level"] > empty[area]["level"]]
        self.assertIn("C1", raised)
        self.assertTrue(all(rated[a]["required"] == empty[a]["required"] for a in rated))  # 기준 단계는 내 답과 무관하다
        with mock.patch("backend.api.required_level", return_value=(DEFAULT_REQUIRED_LEVEL, "uncertain")):
            rows = api.get_level_radar("cloud_engineer", {})
        self.assertTrue(all(r["required"] == DEFAULT_REQUIRED_LEVEL and r["required_uncertain"] == len(r["skills"])
                            for r in rows))


if __name__ == "__main__":
    unittest.main()
