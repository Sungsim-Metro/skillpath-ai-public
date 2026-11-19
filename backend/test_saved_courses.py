"""담은 교육과정의 프로필 저장(2026-11-20). 실행: python -m unittest backend.test_saved_courses"""
import csv
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend import api, chat_profiles

PASSWORD = "test-only passphrase!"
A = {"title": "과정 A", "url": "https://example.com/a", "level": "기초"}
B = {"title": "과정 B", "url": "https://example.com/b", "level": ""}


class SavedCoursesTest(unittest.TestCase):
    def setUp(self):
        path = Path(self.enterContext(TemporaryDirectory())) / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "SKILLPATH_MOCK_PROFILE_DB": str(path)}))
        self.session = chat_profiles.create_profile("saved-course-owner", PASSWORD)

    def test_courses_are_kept_in_order_once_each_and_removed_by_address(self):
        self.assertEqual(chat_profiles.load_saved_courses(self.session), [])
        extra = {**A, "reason": "화면에만 쓰는 값", "match": "certain"}  # 저장하는 것은 title, url, level 뿐이다
        self.assertEqual(chat_profiles.update_saved_courses(self.session, add=[extra, B]), [A, B])
        self.assertEqual(chat_profiles.update_saved_courses(self.session, add=[A]), [A, B])  # 같은 주소는 한 번만
        self.assertEqual(chat_profiles.update_saved_courses(self.session, remove_urls=[A["url"]]), [B])
        self.assertEqual(chat_profiles.update_saved_courses(self.session, add=[A], remove_urls=[A["url"]]), [B])
        again = chat_profiles.sign_in("saved-course-owner", PASSWORD)
        self.assertEqual(chat_profiles.load_saved_courses(again), [B])

    def test_saved_courses_stay_out_of_the_profile_fields_and_survive_profile_updates(self):
        chat_profiles.update_saved_courses(self.session, add=[A])
        self.assertEqual(chat_profiles.load_profile(self.session),
                         {"job": None, "skills": [], "experiences": [], "levels": {}})
        chat_profiles.update_profile(self.session, job="cloud_engineer", add_skills=["Linux"])
        self.assertEqual(chat_profiles.load_saved_courses(self.session), [A])
        chat_profiles.update_saved_courses(self.session, add=[B])
        self.assertEqual(chat_profiles.load_profile(self.session)["skills"], ["Linux"])

    def test_bad_courses_the_limit_and_missing_sessions_are_refused(self):
        for bad in ({**A, "url": "http://example.com/a"}, {**A, "url": "javascript:alert(1)"}, {**A, "title": " "},
                    {"url": A["url"]}, "https://example.com/a", {**A, "level": 3}):
            with self.assertRaises(ValueError):
                chat_profiles.update_saved_courses(self.session, add=[bad])
        with self.assertRaises(ValueError):
            chat_profiles.load_saved_courses("saved-course-owner")
        many = [{**A, "url": f"https://example.com/{n}"} for n in range(chat_profiles.SAVED_COURSE_LIMIT)]
        self.assertEqual(len(chat_profiles.update_saved_courses(self.session, add=many)), chat_profiles.SAVED_COURSE_LIMIT)
        with self.assertRaises(chat_profiles.SavedCourseLimit):
            chat_profiles.update_saved_courses(self.session, add=[B])
        self.assertEqual(chat_profiles.load_saved_courses(self.session), many)  # 넘치면 아무것도 바꾸지 않는다

    def test_cards_get_ratings_from_the_course_table_when_the_address_is_known(self):
        with (Path(api.DATA_DIR) / "courses" / "courses.csv").open(encoding="utf-8-sig", newline="") as f:
            row = next(r for r in csv.DictReader(f) if r["rating_avg"])
        known = {"title": row["title"], "url": row["url"], "level": row["level"]}
        cards = api.get_saved_course_cards([known, A])
        self.assertEqual(cards[0], {**known, "rating_avg": float(row["rating_avg"]), "rating_count": int(row["rating_count"])})
        self.assertEqual(cards[1], A)  # 과정 표에 없는 주소는 받은 그대로


if __name__ == "__main__":
    unittest.main()
