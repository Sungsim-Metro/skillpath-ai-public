"""교육과정 평점 읽기 확인(2026-11-20). 실행: python -m unittest pipeline.test_course_rating"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).with_name("courses")))  # build_courses 는 같은 폴더의 collect_courses 를 가져온다

import build_courses  # noqa: E402

PAGE = """---
course_id: {cid}
source_url: https://example.com/learn/{cid}
---

예시 과정
Digital course
{head}
Description

과정 설명 문장입니다.

Objectives
목표 하나
{ratings}
Overview
"""
RATINGS = "Ratings\naverage rating of {avg} out of 5\n{avg}\ntotal number of reviews {count} reviews\n(1,234)"


def _sections(head: str = "", ratings: str = "") -> dict:
    _, body = build_courses.split_front_matter(PAGE.format(cid="X", head=head, ratings=ratings))
    return build_courses.sections(body)


class ParseRatingTest(unittest.TestCase):
    def test_reads_average_and_count_from_ratings_section(self):
        sec = _sections("4.6 (11143)\nRated 5 out of 5 based on 11,143 reviews", RATINGS.format(avg="4.6", count="11143"))
        self.assertEqual(build_courses.parse_rating(sec), ("4.6", "11143"))

    def test_falls_back_to_the_line_under_the_title(self):
        self.assertEqual(build_courses.parse_rating(_sections("4.8 (1,250)")), ("4.8", "1250"))

    def test_rounded_line_alone_is_not_a_rating(self):
        """'Rated 5 out of 5' 는 반올림한 값이라 평균으로 쓰지 않는다."""
        self.assertEqual(build_courses.parse_rating(_sections("Rated 5 out of 5 based on 3 reviews")), ("", ""))

    def test_missing_or_out_of_range_values_stay_empty(self):
        self.assertEqual(build_courses.parse_rating(_sections()), ("", ""))
        self.assertEqual(build_courses.parse_rating(_sections("0.0 (0)")), ("", ""))
        self.assertEqual(build_courses.parse_rating(_sections(ratings=RATINGS.format(avg="7.2", count="10"))), ("", ""))


class FillRatingsTest(unittest.TestCase):
    def test_only_rating_columns_change_and_missing_pages_keep_old_values(self):
        with tempfile.TemporaryDirectory() as folder:
            raw, table = Path(folder) / "raw", Path(folder) / "courses.csv"
            raw.mkdir()
            rows = [{"course_id": "A", "title": "가", "url": "https://example.com/a"},
                    {"course_id": "B", "title": "나", "url": "https://example.com/b"},
                    {"course_id": "C", "title": "다", "url": "https://example.com/c"}]
            build_courses.write_table(table, rows, list(rows[0]))
            (raw / "A.md").write_text(PAGE.format(cid="A", head="", ratings=RATINGS.format(avg="4.5", count="20")),
                                      encoding="utf-8")
            (raw / "B.md").write_text(PAGE.format(cid="B", head="", ratings=""), encoding="utf-8")  # 평가가 없는 과정
            with mock.patch.object(build_courses, "RAW_DIR", raw), mock.patch.object(build_courses, "COURSES_FILE", table), \
                 mock.patch("builtins.print"):
                build_courses.fill_ratings()
                first = build_courses.read_table(table)
                (raw / "A.md").unlink()  # 원문이 없어도 적혀 있던 값은 남는다
                build_courses.fill_ratings()
                second = build_courses.read_table(table)
        self.assertEqual(list(first[0]), ["course_id", "title", "url", "rating_avg", "rating_count"])
        self.assertEqual([(r["rating_avg"], r["rating_count"]) for r in first], [("4.5", "20"), ("", ""), ("", "")])
        self.assertEqual([{k: r[k] for k in ("course_id", "title", "url")} for r in first], rows)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
