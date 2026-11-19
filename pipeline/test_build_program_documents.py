import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.build_program_documents import build_documents


class ProgramDocumentTest(unittest.TestCase):
    def test_source_and_filter_metadata_for_each_catalog_row(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            output = Path(directory) / "programs"
            with catalog.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["course_id", "title", "description", "objectives", "retiring",
                                 "audience", "prerequisites"])
                writer.writerow(["one", "클라우드 입문", "설명\n둘째 줄", "학습 목표", "", "클라우드 개발자", "기초 네트워크"])
                writer.writerow(["two", "Course two", "Description", "", "", "", ""])
                writer.writerow(["three", "Course three", "Description", "Objectives", "2026-11-30", "", ""])

            self.assertEqual(build_documents(catalog, output, "sample"), 3)
            self.assertEqual(build_documents(catalog, output, "sample"), 3)
            self.assertEqual(len(list(output.iterdir())), 6)
            text = (output / "one.md").read_text(encoding="utf-8")
            for value in ("클라우드 입문", "설명\n둘째 줄", "학습 목표",
                          "## Intended audience\n\n클라우드 개발자", "## Prerequisites\n\n기초 네트워크"):
                self.assertIn(value, text)
            for course_id, recommendable in (("one", True), ("two", False), ("three", False)):
                metadata = json.loads((output / f"{course_id}.md.metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["metadataAttributes"], {
                    "type": "program", "provider": "sample", "course_id": course_id, "recommendable": recommendable,
                })
