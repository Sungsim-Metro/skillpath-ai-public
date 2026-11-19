"""보유 수준 5단계 계산 확인(가짜 모드). 실행: python -m unittest backend.test_levels"""
import os
import unittest
from unittest import mock

os.environ["BEDROCK_MOCK"] = "1"

from backend import api, levels  # noqa: E402

JOB = "cloud_engineer"
OLD = ["Python", "Linux", "Git"]
# 예전 목록과 같은 기술을 진단으로 넣은 값: Python→C5-2, Linux→C1-2, Git→C5-3
NEW = {"C5-2": 3, "C1-2": 3, "C5-3": 3}


class LevelTest(unittest.TestCase):
    def setUp(self):
        # 기준 단계 파일과 상관없이 확인한다: 모든 기술 기준 3, 불확실
        patcher = mock.patch.object(levels, "required_levels", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_skill_level_from_subskills(self):
        self.assertEqual(levels.skill_level(NEW, "Python"), 3)       # 연결표(C5-2)
        self.assertEqual(levels.skill_level(NEW, "Linux"), 3)        # 별칭(C1-2)
        self.assertEqual(levels.skill_level({"C1-4": 2}, "Docker"), 2)
        self.assertEqual(levels.skill_level(NEW, "Docker"), 0)       # 진단 안 한 세부 능력은 0
        self.assertEqual(levels.skill_level({"C1-1": 5, "C5-2": 2}, "AWS"), 3.5)  # 공통 기술은 평균
        self.assertEqual(levels.skill_level({}, "AWS"), 0)
        self.assertEqual(levels.skill_level({"C1-1": 4, "C2-1": 0}, "AWS"), 4)  # 0은 평균에서 뺀다

    def test_satisfaction_required_threshold_and_preferred_scale(self):
        # 필수 3건, 우대 1건, 기준 3: 기준을 넘으면 필수 몫(0.75) 전부 + 우대 몫(0.25) × 단계/5
        self.assertEqual(levels.satisfaction(3, 3, 3, 1), round(0.75 + 0.25 * 3 / 5, 2))
        self.assertEqual(levels.satisfaction(2, 3, 3, 1), round(0.25 * 2 / 5, 2))
        self.assertEqual(levels.satisfaction(5, 3, 3, 1), 1.0)
        self.assertEqual(levels.satisfaction(0, 3, 3, 1), 0.0)

    def test_old_list_input_gives_same_result_as_before(self):
        rows = {r["skill"]: r for r in api.get_gap(JOB, OLD)}
        self.assertTrue(rows["Python"]["owned"] and not rows["Docker"]["owned"])
        self.assertEqual(rows["Python"]["satisfaction"], 1.0)
        demand = {s["skill"]: s["postings"] for s in api.get_demand(JOB)["skills"]}
        compute = {r["area"]: r for r in api.get_area_gap(JOB, OLD)}["컴퓨팅·서버"]
        self.assertEqual(compute["owned"], demand["Linux"])
        self.assertEqual(compute["missing_skills"], ["Docker", "Kubernetes"])

    def test_levels_input(self):
        rows = {r["skill"]: r for r in api.get_gap(JOB, {**NEW, "C1-4": 2})}
        self.assertTrue(rows["Python"]["owned"])
        self.assertEqual(rows["Python"]["user_level"], 3)
        self.assertEqual(rows["Docker"]["user_level"], 2)
        self.assertFalse(rows["Docker"]["owned"])
        self.assertEqual(rows["Docker"]["required_level"], 3)
        self.assertEqual(rows["Docker"]["required_level_confidence"], "uncertain")
        self.assertGreater(rows["Docker"]["satisfaction"], 0)        # 우대 몫은 단계만큼 채운다
        rows = {r["skill"]: r for r in api.get_gap(JOB, {"C5-2": 2})}
        self.assertFalse(rows["Python"]["owned"])
        # 영역 몫은 소수일 수 있다
        area = {r["area"]: r for r in api.get_area_gap(JOB, {"C1-4": 2})}["컴퓨팅·서버"]
        self.assertIn("Docker", area["missing_skills"])
        self.assertGreater(area["owned"], 0)

    def test_required_level_file_is_used(self):
        with mock.patch.object(levels, "required_levels",
                               return_value={"Python": {"required_level": 4, "confidence": "certain"}}):
            row = {r["skill"]: r for r in api.get_gap(JOB, NEW)}["Python"]
        self.assertEqual((row["required_level"], row["required_level_confidence"]), (4, "certain"))
        self.assertFalse(row["owned"])

    def test_invalid_levels_raise(self):
        for bad in ({"C5-2": 6}, {"C5-2": -1}, {"C5-2": 2.5}, {"C5-2": True}, {"없는-id": 1}):
            with self.assertRaises(ValueError):
                api.get_gap(JOB, bad)

    def test_named_levels_update_by_exact_name_across_both_diagnoses(self):
        original = {"C1-4": 4, "A1-1": 1, "custom:Docker": 3, "custom:C++": 5}
        result = levels.merge_levels(original, {"custom: docker ": 0, "custom:C#": 2})
        self.assertEqual(result, {**original, "custom:Docker": 0, "custom:C#": 2})

    def test_invalid_named_level(self):
        for key, value in (("custom:  ", 2), ("custom:two\nlines", 3), ("custom:" + "가" * 121, 2), ("custom:C++", True),
                           ("custom:SQL", 6)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                levels.validate_levels({key: value})

    def test_recommendation_order_follows_level(self):
        missing = [r["skill"] for r in api.get_gap(JOB, NEW) if not r["owned"]]
        plain = api.get_recommendations(JOB, missing)
        self.assertTrue(all("user_level" not in r for r in plain))   # 단계를 안 주면 전과 같다
        for level, first in ((1, "기초"), (4, "중급")):
            recs = api.get_recommendations(JOB, missing, levels={s: level for s in missing})
            for rec, before in zip(recs, plain):
                self.assertEqual(rec["user_level"], level)
                self.assertEqual(sorted(c["title"] for c in rec["courses"]),
                                 sorted(c["title"] for c in before["courses"]))  # 과정 수와 종류는 그대로
                sure = [c for c in rec["courses"] if c["match"] != "uncertain"]
                if any(c["level"] == first for c in sure):
                    self.assertEqual(sure[0]["level"], first)


if __name__ == "__main__":
    unittest.main()
