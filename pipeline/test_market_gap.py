"""시장 Gap 파일 입력, 대시보드 조회 및 배치 출력 회귀 검사."""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import market_gap


class MarketGapFilesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def put_json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def put_programs(self, rows=(), name="data/courses/courses.csv"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["course_id", "title", "description", "objectives", "prerequisites"])
            writer.writerows(rows)

    def test_supply_can_be_exported_and_read_without_postings(self):
        from backend import api

        self.put_programs([("a", "Docker", "도커", "SQL", ""), ("b", "SQL", "", "", ""),
                           ("c", "기초 과정", "", "", "Docker")])
        with patch.object(api, "DATA_DIR", self.root / "data"), patch.object(api, "is_mock", return_value=False):
            expected = api.get_supply()
            self.assertEqual(expected["total_courses"], 3)
            self.assertEqual({r["skill"]: r["courses"] for r in expected["skills"]}, {"Docker": 1, "SQL": 2})
            market_gap.main(["--root", str(self.root), "--supply-only"])
            snapshot = self.root / market_gap.SUPPLY
            self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")), expected)
            self.assertEqual(list(snapshot.parent.iterdir()), [snapshot])
            (self.root / "data/courses/courses.csv").rename(self.root / "archived.csv")
            self.assertEqual(api.get_supply(), expected)
            self.put_json("outputs/postings_clean.json", [{"job_category": "cloud_engineer", "text": "SQL"}])
            market = api.get_market_gap("cloud_engineer")
            self.assertEqual({r["skill"]: r["supply_pct"] for r in market["rows"]},
                             {"Docker": 33.3, "SQL": 66.7})

    def test_sample_supply_is_only_used_in_mock_mode_without_catalogs(self):
        sample = {"source": "sample", "total_courses": 5, "skills": [{"skill": "Docker", "courses": 2}]}
        self.put_json("data/sample/supply.json", sample)
        self.assertEqual(market_gap.dashboard_supply(self.root, allow_example=True), sample)
        with self.assertRaises(FileNotFoundError):
            market_gap.dashboard_supply(self.root, allow_example=False)
        self.put_programs([])
        self.assertEqual(market_gap.dashboard_supply(self.root, allow_example=True)["total_courses"], 0)
        self.put_json(market_gap.SUPPLY, {**sample, "skills": [{"skill": "SQL", "courses": 6}]})
        with self.assertRaisesRegex(ValueError, "과정 수"):
            market_gap.dashboard_supply(self.root, allow_example=True)

    def test_zero_course_snapshot_gives_demanded_skill_zero_supply(self):
        self.put_json(market_gap.SUPPLY, {
            "source": "빈 목록", "total_courses": 0, "skills": [{"skill": "Docker", "courses": 0}],
        })
        self.put_json("outputs/postings_clean.json", [{"job_category": "cloud_engineer", "text": "Docker"}])

        result = market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)

        self.assertEqual(result["rows"], [
            {"job_category": "cloud_engineer", "skill": "Docker", "demand_pct": 100.0,
             "supply_pct": 0.0, "gap_pp": 100.0},
        ])

    def test_clean_input_wins_and_is_not_cleaned_twice(self):
        self.put_json("outputs/postings_clean.json", [
            {"id": "1", "job_category": "cloud_engineer", "text": "<Docker>", "skills_rule": ["SQL"], "title": "Java"},
            {"id": "1", "job_category": "cloud_engineer", "text": "<Docker>"},
            {"id": "2", "job_category": "cloud_engineer", "text": "AWS"},
            {"id": "1", "job_category": "ai_engineer", "text": "LLM"},
        ])
        self.put_json("data/raw/job_postings_raw.json", {})
        self.put_programs()
        result = market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)
        self.assertEqual([(row["skill"], row["demand_pct"]) for row in result["rows"]],
                         [("AWS", 50.0), ("Docker", 50.0)])
        ai = market_gap.dashboard_gap(self.root, "ai_engineer", allow_example=False)
        self.assertEqual([(row["skill"], row["demand_pct"]) for row in ai["rows"]], [("LLM", 100.0)])

    def test_raw_fallback_then_dashboard_reads_export_until_rebuilt(self):
        self.put_json("data/raw/job_postings_raw.json", {"response": {"items": [
            {"role": "클라우드 엔지니어", "jobCont": "[자격요건]<br>Docker\nAWS\n[주요업무]\n k8s &nbsp; AWS"},
            {"role": "AI 엔지니어", "jobCont": "<b>SQL</b>"},
        ]}})
        self.put_programs([("p1", "파이썬", "Python", "", "Docker")])
        before = market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)
        self.assertEqual([(r["skill"], r["gap_pp"]) for r in before["rows"]],
                         [("AWS", 58.8), ("Kubernetes", 41.2), ("Python", -100.0)])
        args = ["--root", str(self.root)]
        market_gap.main(args)
        self.put_json("outputs/postings_clean.json", [])
        after = market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)
        self.assertEqual(after["rows"], before["rows"])
        ai_rows = market_gap.dashboard_gap(self.root, "ai_engineer", allow_example=False)["rows"]
        self.assertEqual([(row["job_category"], row["skill"], row["gap_pp"]) for row in ai_rows],
                         [("ai_engineer", "Python", -100.0), ("ai_engineer", "SQL", 100.0)])
        output = self.root / "outputs"
        self.assertTrue((output / "learning_program_job_gap_table.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        report = (output / "learning_program_job_gap_report.md").read_text(encoding="utf-8")
        self.assertIn("클라우드 엔지니어", report)
        self.assertIn("AI 엔지니어", report)
        self.assertIn("AWS Skill Builder", report)
        self.assertIn("-100.0", report)
        market_gap.main(args)
        self.assertEqual(market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)["rows"], [])

    def test_missing_partial_or_malformed_sources_do_not_become_empty_supply(self):
        with self.assertRaises(FileNotFoundError):
            market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)
        self.put_json("outputs/postings_clean.json", [])
        with self.assertRaisesRegex(FileNotFoundError, "courses.csv"):
            market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=True)
        self.put_programs()
        (self.root / "data/courses/courses.csv").write_text("title,prerequisites\nAWS,Python\n")
        with self.assertRaisesRegex(ValueError, "objectives"):
            market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=True)

    def test_mock_postings_still_use_the_registered_catalog_and_ignore_legacy_results(self):
        from backend import api

        self.put_programs([("l1", "Linux", "Linux", "", "Docker")])
        legacy = self.root / "outputs/learning_program_gap_table.csv"
        legacy.parent.mkdir()
        legacy.write_text("industry,skill,demand_pct,supply_pct,gap_pp\n예시,Python,0.0,100.0,-100.0\n")
        with patch.object(api, "DATA_DIR", self.root / "data"), patch.object(api, "is_mock", return_value=True):
            market = api.get_market_gap("cloud_engineer")
        self.assertIn("예시", market["source"])
        self.assertIn("AWS Skill Builder", market["source"])
        supply = {r["skill"]: r["supply_pct"] for r in market["rows"]}
        self.assertEqual(supply, {"AWS": 0.0, "Docker": 0.0, "Kubernetes": 0.0, "Linux": 100.0})
        market_gap.main(["--root", str(self.root), "--example"])
        example = self.root / "outputs/example/learning_program_job_gap_table.csv"
        with example.open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
            self.assertEqual({row["job_category"] for row in rows}, {"ai_engineer", "cloud_engineer"})
            self.assertEqual(sum(row["job_category"] == "cloud_engineer" for row in rows), 4)
        self.assertFalse((self.root / "outputs/learning_program_job_gap_table.csv").exists())

    def test_supply_combines_programs_across_providers_before_normalizing(self):
        self.put_json("outputs/postings_clean.json", [{"job_category": "cloud_engineer", "text": "AWS"}])
        self.put_programs([("1", "SQL", "SQL", "", "Azure"), ("2", "Docker", "", "", "")])
        self.put_programs([("1", "SQL", "", "", "")], "data/programs/partner.csv")
        catalogs = {"공급처 A": Path("data/courses/courses.csv"), "공급처 B": Path("data/programs/partner.csv")}
        with patch.object(market_gap, "PROGRAM_CATALOGS", catalogs):
            result = market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=False)
        self.assertEqual({r["skill"]: r["supply_pct"] for r in result["rows"]},
                         {"AWS": 0.0, "Docker": 33.3, "SQL": 66.7})
        for provider in catalogs:
            self.assertIn(provider, result["source"])

    def test_shared_reviewed_posting_counts_once_in_each_role(self):
        reviewed = market_gap.REVIEWED
        shared = {"id": "rp-1", "industry": None, "text": "Docker"}
        self.put_json(reviewed / "ai/rp-1.posting.json", shared)
        self.put_json(reviewed / "cloud/rp-1.posting.json", shared)
        self.put_json(reviewed / "cloud/rp-2.posting.json", {"id": "rp-2", "industry": "IT", "text": "AWS"})
        self.put_json(market_gap.CLEAN, [{"industry": "IT", "text": "Java"}])
        self.put_programs()

        for job, expected, count in [
            ("ai_engineer", [("Docker", 100.0)], 1),
            ("cloud_engineer", [("AWS", 50.0), ("Docker", 50.0)], 2),
        ]:
            with self.subTest(job=job):
                result = market_gap.dashboard_gap(self.root, job, allow_example=True)
                self.assertEqual([(row["skill"], row["demand_pct"]) for row in result["rows"]], expected)
                self.assertEqual({row["job_category"] for row in result["rows"]}, {job})
                self.assertIn(f"{count}건", result["source"])

        self.put_json(market_gap.CLEAN, [])
        self.assertEqual(market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=True)["rows"], [])

    def test_unlabelled_postings_alone_cannot_be_assigned_to_a_role(self):
        self.put_json(market_gap.RAW, {"response": {"items": [{"indTpNm": "IT", "jobCont": "Docker"}]}})
        self.put_programs()
        with self.assertRaisesRegex(ValueError, "직무"):
            market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=True)

    def test_unknown_job_category_rejected_before_reading_data(self):
        with self.assertRaisesRegex(ValueError, "직무"):
            market_gap.dashboard_gap(self.root, "unsupported", allow_example=True)

    def test_partial_reviewed_folders_do_not_fall_back_to_example(self):
        reviewed = self.root / "data/raw/rocketpunch-2026-11-17/postings-reviewed"
        (reviewed / "ai").mkdir(parents=True)
        self.put_programs()
        with self.assertRaisesRegex(FileNotFoundError, "cloud"):
            market_gap.dashboard_gap(self.root, "cloud_engineer", allow_example=True)


if __name__ == "__main__":
    unittest.main()
