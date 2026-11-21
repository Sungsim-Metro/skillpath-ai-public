"""추천 결과에 평점이 선택 필드로 실리는지 확인(2026-11-20). 실행: python -m unittest ai.test_recommend_rating

Bedrock 을 부르지 않는다. 과정 표와 검색 결과를 바꿔 끼운다.
"""
import os
import unittest
from unittest import mock

import ai.recommend as rec

COURSE = {"title": "예시 과정", "url": "https://example.com/course", "level": "기초", "prerequisites": "",
          "objectives": "컨테이너 이미지를 만든다 / 서비스를 배포한다", "retiring": ""}
TABLE = {
    "RATED": {**COURSE, "rating_avg": "4.6", "rating_count": "1280"},
    "EMPTY": {**COURSE, "rating_avg": "", "rating_count": ""},
    "OLD": dict(COURSE),  # 평점 열이 생기기 전의 표
}
LINKS = [{"course_id": cid, "subskill_id": "C1-4", "match": "certain"} for cid in TABLE]


class CourseRatingTest(unittest.TestCase):
    def test_only_valid_ratings_become_fields(self):
        self.assertEqual(rec.course_rating(TABLE["RATED"]), {"rating_avg": 4.6, "rating_count": 1280})
        for row in (TABLE["EMPTY"], TABLE["OLD"], {"rating_avg": "4.6", "rating_count": ""},
                    {"rating_avg": "abc", "rating_count": "3"}, {"rating_avg": "0", "rating_count": "3"},
                    {"rating_avg": "5.5", "rating_count": "3"}, {"rating_avg": "4.0", "rating_count": "0"}):
            self.assertEqual(rec.course_rating(row), {}, row)

    def test_rating_reaches_the_recommendation_but_not_the_llm_input(self):
        demand = {"postings": 20, "of": 40, "required_ratio": 0.5}
        with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), \
             mock.patch.object(rec, "_courses", return_value=TABLE), \
             mock.patch.object(rec, "search_courses", return_value=LINKS), \
             mock.patch.object(rec, "_mock_skills", return_value=set()):
            courses = rec.candidate_courses("예시기술")
            llm_input = rec.llm_input_of("예시기술", demand, courses)
            result, _ = rec.build_recommendation("예시기술", demand)
        self.assertEqual([("rating_avg" in c, "rating_count" in c) for c in courses],
                         [(True, True), (False, False), (False, False)])
        self.assertFalse(any("rating_avg" in c or "rating_count" in c for c in llm_input["courses"]))
        shown = result["courses"]
        self.assertEqual(len(shown), 3)
        self.assertEqual((shown[0]["rating_avg"], shown[0]["rating_count"]), (4.6, 1280))
        self.assertNotIn("rating_avg", shown[1])
        self.assertNotIn("rating_count", shown[2])


class CourseInfoTest(unittest.TestCase):
    """소요 시간·강의 언어·AWS 서비스는 화면 표시용 선택 필드다(2026-11-21). 추천까지 가고 LLM 입력에는 들어가지 않는다."""

    FULL = {**COURSE, "duration_min": "90", "language": "English",
            "aws_services": "Amazon Elastic Compute Cloud / AWS Config"}

    def test_only_present_values_become_fields(self):
        self.assertEqual(rec.course_info(self.FULL), {"duration_min": 90, "language": "English",
                                                      "aws_services": ["Amazon Elastic Compute Cloud", "AWS Config"]})
        for row in (dict(COURSE), {"duration_min": "", "language": " ", "aws_services": ""},
                    {"duration_min": "abc"}, {"duration_min": "0"}):
            self.assertEqual(rec.course_info(row), {}, row)

    def test_info_reaches_the_recommendation_but_not_the_llm_input(self):
        demand = {"postings": 20, "of": 40, "required_ratio": 0.5}
        table = {"FULL": self.FULL, "OLD": dict(COURSE)}
        links = [{"course_id": cid, "subskill_id": "C1-4", "match": "certain"} for cid in table]
        with mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), \
             mock.patch.object(rec, "_courses", return_value=table), \
             mock.patch.object(rec, "search_courses", return_value=links), \
             mock.patch.object(rec, "_mock_skills", return_value=set()):
            courses = rec.candidate_courses("예시기술")
            llm_input = rec.llm_input_of("예시기술", demand, courses)
            result, _ = rec.build_recommendation("예시기술", demand)
        self.assertFalse(any(key in c for c in llm_input["courses"] for key in rec.INFO_KEYS))
        full, old = result["courses"]
        self.assertEqual((full["duration_min"], full["language"], full["aws_services"]),
                         (90, "English", ["Amazon Elastic Compute Cloud", "AWS Config"]))
        self.assertFalse(any(key in old for key in rec.INFO_KEYS))


if __name__ == "__main__":
    unittest.main()
