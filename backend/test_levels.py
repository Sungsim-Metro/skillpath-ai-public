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
        for job in ("cloud_engineer", "ai_engineer"):
            self.assertEqual(levels.skill_levels(result, ["Docker", "C++", "C#"], job),
                             {"Docker": 0, "C++": 5, "C#": 2})
        self.assertEqual(levels.skill_levels(result, ["AWS"], "cloud_engineer"), {"AWS": 4})
        self.assertEqual(levels.skill_levels(result, ["AWS"], "ai_engineer"), {"AWS": 1})

    def test_named_level_in_gap_and_ownership(self):
        mine = {"custom:도커": 4}
        row = next(row for row in api.get_gap(JOB, mine) if row["skill"] == "Docker")
        self.assertEqual(row["user_level"], 4)
        self.assertTrue(row["owned"])
        self.assertIn("Docker", api.owned_skill_names(JOB, mine))
        lowered = levels.merge_levels(mine, {"custom:docker": 0, "C1-4": 5})
        self.assertEqual(lowered, {"custom:Docker": 0, "C1-4": 5})
        self.assertNotIn("Docker", api.owned_skill_names(JOB, lowered))

    def test_invalid_named_level(self):
        for key, value in (("custom:  ", 2), ("custom:two\nlines", 3), ("custom:" + "가" * 121, 2), ("custom:C++", True),
                           ("custom:SQL", 6)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                levels.validate_levels({key: value})

    def test_assessment_questions_group_confirmed_skills_in_survey_order(self):
        skills = ["도커", "Podman", "docker", "Kubernetes", "파이썬", "AWS", "OpenTofu", "Jenkins"]
        questions = levels.assessment_questions(skills, "ai_engineer", {})
        self.assertEqual(list(questions.items()), [
            ("A2-1", ["Python"]), ("A2-2", ["Python"]), ("A4-2", ["Docker", "Kubernetes"]),
        ])
        self.assertEqual(levels.assessment_questions(["도커", "AWS", "Podman"], JOB, {}), {"C1-4": ["Docker"]})

    def test_assessment_questions_keep_answered_items_and_append_direct_ratings_in_input_order(self):
        answers = {"A2-1": 0, "A4-2": 4, "custom:Python": 3, "custom:도커": 0,
                   "custom:Jenkins": 2, "custom:Podman": 4, "custom:AWS": 5}
        skills = ["도커", "Podman", "Jenkins", "파이썬", "Kubernetes", "Docker", "AWS"]
        questions = levels.assessment_questions(skills, "ai_engineer", answers)
        self.assertEqual(list(questions.items()), [
            ("A2-1", ["Python"]), ("A2-2", ["Python"]), ("A4-2", ["Docker", "Kubernetes"]),
            ("custom:Docker", ["Docker"]), ("custom:Python", ["Python"]),
        ])
        validated = levels.validate_levels(answers)
        self.assertEqual([validated[key] for key in questions if key.startswith("custom:")], [0, 3])

    def test_assessment_question_preserves_stored_spelling_for_mapped_names_outside_alias_dictionary(self):
        questions = levels.assessment_questions(["virtual server", "VIRTUAL SERVER"], JOB, {"custom:Virtual Server": 0})
        self.assertEqual(list(questions.items()), [
            ("C1-1", ["Virtual Server"]), ("custom:Virtual Server", ["Virtual Server"]),
        ])

    def test_skill_rating_requires_a_direct_or_contributing_answer_even_at_zero(self):
        cases = [
            ({"custom:OpenTofu": 0}, "opentofu", JOB, True),
            ({"custom:도커": 0}, "docker", "ai_engineer", True),
            ({"custom:Docker": 4}, "Podman", JOB, False),
            ({"C1-4": 0}, "Docker", "ai_engineer", True),
            ({"A4-2": 0}, "도커", "ai_engineer", True),
            ({"A4-2": 5}, "Docker", JOB, False),
            ({"A2-2": 0}, "Python", "ai_engineer", True),
            ({"C1-4": 5}, "OpenTofu", JOB, False),
            ({"C1-4": 0}, "AWS", JOB, True),
            ({"A2-2": 5, "custom:Podman": 2}, "AWS", JOB, False),
        ]
        for answers, skill, job, expected in cases:
            with self.subTest(answers=answers, skill=skill, job=job):
                self.assertEqual(levels.has_skill_rating(answers, skill, job), expected)

    def test_ai_diagnosis_uses_the_same_survey_level_for_an_explicit_alias(self):
        self.assertEqual(levels.skill_level({"A4-2": 3}, "도커", "ai_engineer"), 3)

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
