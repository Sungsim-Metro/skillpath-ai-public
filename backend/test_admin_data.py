"""관리자 화면이 읽는 값: 수요 집계 현황, 과정 커버리지, 평가 결과 파일. 실행: python -m unittest backend.test_admin_data"""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend import api, eval_results

DEMAND = {"job_category": "cloud_engineer", "total_postings": 4, "sources": ["rocketpunch"], "collected": "2026-11-19",
          "skills": [{"skill": "AWS", "postings": 3}, {"skill": "Go", "postings": 2}, {"skill": "Rust", "postings": 1},
                     {"skill": "Broken", "postings": 1}]}


class AdminDataTest(unittest.TestCase):
    def test_demand_status_passes_quality_through_and_sorts_uncertain_skills(self):
        snapshot = {
            "generated_at": "2026-11-19T12:00:00Z", "policy": {"selection_version": "v1"},
            "demand": {**DEMAND, "total_postings": Decimal(4)},
            "quality": {"empty_extraction_postings": Decimal(1), "uncertain_only_postings": Decimal(2),
                        "excluded_postings": [{"posting_id": "rp-1", "reason": "직무 아님"}],
                        "uncertain_skills": [{"skill": "RAG", "postings": Decimal(2)}, {"skill": "PyTorch", "postings": Decimal(5)}]},
        }
        with patch.dict(os.environ, {"BEDROCK_MOCK": "0"}), patch.object(api.DynamoDBStore, "from_env") as store:
            store.return_value.get_demand_snapshot.return_value = snapshot
            status = api.get_demand_status("cloud_engineer")
            store.return_value.get_demand_snapshot.return_value = None
            with self.assertRaises(LookupError):
                api.get_demand_status("cloud_engineer")
        self.assertEqual((status["total_postings"], status["counted_skills"], status["selection_version"]), (4, 4, "v1"))
        self.assertEqual((status["empty_extraction_postings"], status["uncertain_only_postings"]), (1, 2))
        self.assertEqual([row["skill"] for row in status["uncertain_skills"]], ["PyTorch", "RAG"])   # 많이 나온 순
        self.assertEqual(status["excluded_postings"][0]["reason"], "직무 아님")
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), self.assertRaises(NotImplementedError):
            api.get_demand_status("cloud_engineer")                                                  # 예시 집계에는 품질 값이 없다

    def test_course_coverage_has_three_states_and_survives_one_bad_skill(self):
        def courses(skill, rules_only=False):
            self.assertTrue(rules_only)                                   # 모델과 검색 서비스를 부르지 않는 길만 쓴다
            if skill == "Broken":
                raise KeyError("과정 데이터와 맞지 않음")
            return {"AWS": [{"match": "certain"}, {"match": "uncertain"}], "Go": [{"match": "uncertain"}]}.get(skill, [])

        with patch.object(api, "get_demand", return_value=DEMAND), patch("ai.recommend.candidate_courses", side_effect=courses):
            rows = {row["skill"]: row for row in api.get_course_coverage("cloud_engineer")}
        self.assertEqual({skill: row["status"] for skill, row in rows.items()},
                         {"AWS": "certain", "Go": "uncertain", "Rust": "none", "Broken": "unknown"})
        self.assertEqual((rows["AWS"]["ratio"], rows["AWS"]["courses"], rows["Broken"]["courses"]), (0.75, 2, None))
        with patch.object(api, "get_demand", return_value={**DEMAND, "total_postings": 0, "skills": DEMAND["skills"][:1]}), \
                patch("ai.recommend.candidate_courses", side_effect=courses):
            self.assertEqual(api.get_course_coverage("cloud_engineer")[0]["ratio"], 0)     # 공고가 0건이면 비율은 0

    def test_eval_results_tolerate_missing_folder_and_broken_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(eval_results.list_results(folder / "없는 폴더"), [])
            (folder / "agent_compare.json").write_text(json.dumps({
                "cases": [1, 2, 3], "result": {"0": {"summary": {"intent_correct": 7, "tokens": 100, "rows": [1]}, "rows": []}},
            }), encoding="utf-8")
            (folder / "broken.json").write_text("{", encoding="utf-8")
            results = {item["file"]: item for item in eval_results.list_results(folder)}
            self.assertEqual(results["agent_compare.json"]["cases"], 3)
            self.assertEqual(results["agent_compare.json"]["rows"], [{"이름": "0", "intent_correct": 7, "tokens": 100}])
            self.assertEqual((results["broken.json"]["rows"], results["broken.json"]["error"]), ([], "JSONDecodeError"))
            log = folder / "log.md"
            log.write_text("# 기록\n\n## 발표용 요약\n\n| 영역 | 숫자 |\n| --- | --- |\n| 추출 | 0.9 |\n\n## 항목\n내용\n", encoding="utf-8")
            self.assertEqual(eval_results.summary_table(log), "| 영역 | 숫자 |\n| --- | --- |\n| 추출 | 0.9 |")
            log.write_text("# 기록\n\n## 항목\n내용\n", encoding="utf-8")
            self.assertEqual(eval_results.summary_table(log), "")
            self.assertEqual(eval_results.summary_table(folder / "없는 파일.md"), "")


if __name__ == "__main__":
    unittest.main()
