"""Exercise course difficulty through the same search and review path as chat."""
import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai import recommend
from pipeline import search


def row(cid, title, level, **fields):
    return {"course_id": cid, "title": title, "level": level, "status": "ACTIVE", "retiring": "",
            "url": f"https://example.com/{cid}", "description": title, "prerequisites": "",
            "objectives": f"Deploy {title}.", "aws_services": "", **fields}


class CourseDifficultyTest(unittest.TestCase):
    def setUp(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        primary = [row(f"BASE{i}", f"EC2 Intro {i}", "기초") for i in range(4)]
        primary.append(row("MID", "EC2 operations", "중급"))
        expanded = [row("ADV", "AWS EC2 architecture", "고급"),
                    row("OTHER", "Deep learning", "고급"), row("LAB", "AWS advanced EC2 lab", "고급"),
                    row("INACTIVE", "EC2 archived", "고급", status="ARCHIVED"),
                    row("RET", "EC2 old", "고급", retiring="yes"), row("EMPTY", "EC2 lab", "고급", objectives="")]
        for path, rows in [(temp / "courses.csv", primary), (temp / "catalog_all.csv", expanded)]:
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
        self.enterContext(patch.object(search, "COURSES", temp / "courses.csv"))
        self.enterContext(patch.object(recommend, "COURSES_CSV", temp / "courses.csv"))
        self.enterContext(patch.dict(os.environ, BEDROCK_MOCK="1", PYTHON_DOTENV_DISABLED="1"))
        for loader in (search._courses, recommend._courses):
            loader.cache_clear()
            self.addCleanup(loader.cache_clear)

    def test_exact_level_is_applied_before_top_k_and_review(self):
        for level, ids in (("중급", {"MID"}), ("고급", {"ADV", "LAB"})):
            with self.subTest(level=level):
                cards, _ = recommend.build_recommendation("EC2", None, review=True, course_levels=(level,))
                self.assertEqual({c["level"] for c in cards["courses"]}, {level})
                self.assertEqual({c["url"].split("/")[-1] for c in cards["courses"]}, ids)
                self.assertTrue(all("Deploy " in c["reason"] for c in cards["courses"]))
        self.assertEqual(recommend.candidate_courses("Kubernetes", course_levels=("고급",)), [])
        self.assertEqual(recommend.candidate_courses("EC2", course_levels=()), [])

    def test_advanced_aws_uses_relevant_courses_in_the_expanded_catalog(self):
        selected = recommend.candidate_courses("AWS", course_levels=("고급",))
        self.assertEqual({c["course_id"] for c in selected}, {"ADV", "LAB"})

    def test_primary_catalog_is_used_when_expanded_snapshot_is_not_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "courses.csv"
            primary.write_bytes(search.COURSES.read_bytes())
            with patch.object(search, "COURSES", primary), patch.object(recommend, "COURSES_CSV", primary):
                self.assertEqual([c["course_id"] for c in recommend.candidate_courses("EC2", course_levels=("중급",))],
                                 ["MID"])
                self.assertEqual(recommend.candidate_courses("EC2", course_levels=("고급",)), [])

    def test_real_search_reorders_matching_levels_using_kb_ids_without_level_metadata(self):
        client = Mock()
        client.retrieve.return_value = {"retrievalResults": [
            {"metadata": {"course_id": "BASE0"}, "score": 1},
            {"metadata": {"course_id": "OTHER"}, "score": 0.99},
            {"metadata": {"course_id": "LAB"}, "score": 0.95}]}
        with patch.dict(os.environ, BEDROCK_MOCK="0", BEDROCK_KB_ID="example"), patch.object(
            search, "_kb_client", return_value=client,
        ):
            selected = search.search_courses("EC2", k=1, course_levels=("고급",))
        self.assertEqual([c["course_id"] for c in selected], ["LAB"])
        client.retrieve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
