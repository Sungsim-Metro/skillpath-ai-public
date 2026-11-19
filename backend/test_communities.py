"""현직자 연결: 모임 짝짓기와 검색 링크. 실행: python -m backend.test_communities"""
import os
import unittest
from unittest import mock
from urllib.parse import unquote

from backend import api, communities
from backend.jobs import JOBS

JOB = "cloud_engineer"


class CommunitiesTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"BEDROCK_MOCK": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_data_file_has_no_people_and_valid_areas(self):
        data = communities.load()
        areas = {a for job in JOBS for a in api._skill_areas(job)["areas"]}  # 영역 id 는 직무끼리 겹치지 않는다
        self.assertTrue(data["checked"] and data["source"] and data["limits"])
        for c in data["communities"]:
            self.assertEqual(set(c), {"id", "name", "org", "areas", "kind", "place", "link", "last_seen", "note"})
            self.assertLessEqual(set(c["areas"]), areas, c["id"])
            self.assertIn(c["kind"], ("topic", "general", "region"))
            self.assertTrue(c["link"].startswith("https://"))
            self.assertEqual(bool(c["areas"]), c["kind"] == "topic", c["id"])

    def test_suggest_follows_gap(self):
        found = communities.suggest(api.get_area_gap(JOB, ["Python", "Linux", "Git"]))
        ids = [t["community"]["id"] for t in found["topic"]]
        self.assertIn("container", ids)            # 컴퓨팅·서버가 부족하다
        self.assertNotIn("네트워크", found["unmatched"])  # 네트워크 소모임이 있다(2026-11-17 재확인)
        gaps = [{"area_id": a, "area": a, "demand": 10, "owned": 0} for a in ("C6", "C8")]
        self.assertEqual(communities.suggest(gaps)["unmatched"], ["C8"])  # 맞는 소모임이 없는 영역은 없다고 알린다
        many = [{"area_id": a, "area": a, "demand": d, "owned": 0} for a, d in (("C1", 30), ("C7", 20), ("C5", 19), ("C4", 18))]
        wide = communities.suggest(many)
        self.assertEqual([t["areas"][0] for t in wide["topic"]], ["C1", "C7", "C5"])  # 영역마다 하나씩 먼저
        self.assertEqual(wide["skipped"], ["C4"])                                    # 자리가 없어 빠진 영역은 알린다
        self.assertLessEqual(len(ids), communities.MAX_TOPIC)
        owned_all = [s["skill"] for s in api.get_demand(JOB)["skills"]]
        full = communities.suggest(api.get_area_gap(JOB, owned_all))
        self.assertEqual(full["topic"], [])
        self.assertEqual(full["unmatched"], [])
        self.assertTrue(full["general"])           # 입문·대학생 모임은 늘 있다

    def test_bigger_gap_comes_first(self):
        rows = [
            {"area_id": "C4", "area": "보안", "demand": 10, "owned": 9},
            {"area_id": "C7", "area": "아키텍처 설계", "demand": 10, "owned": 0},
        ]
        found = communities.suggest(rows)
        self.assertEqual(found["topic"][0]["areas"], ["아키텍처 설계"])

    def test_ai_job_gets_ai_communities(self):
        found = communities.suggest(api.get_area_gap("ai_engineer", []))
        self.assertTrue(found["topic"])
        cloud_only = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"}
        for t in found["topic"]:  # AI 영역에 짝지어진 모임만 나온다
            self.assertTrue(set(t["community"]["areas"]) - cloud_only, t["community"]["id"])
        gaps = [{"area_id": "C2", "area": "스토리지·데이터베이스", "demand": 10, "owned": 0}]
        self.assertEqual(communities.suggest(gaps)["unmatched"], ["스토리지·데이터베이스"])  # 짝이 약해 뺀 영역(리뷰 반영)

    def test_people_links_only_build_urls(self):
        links = communities.people_search_links("클라우드 엔지니어", ["AWS", "Docker", "Kubernetes", "Terraform"])
        self.assertEqual(len(links), 3)
        self.assertTrue(all(link["url"].startswith(communities.LINKEDIN_PEOPLE_SEARCH) for link in links))
        self.assertEqual(unquote(links[0]["url"]), communities.LINKEDIN_PEOPLE_SEARCH + "클라우드 엔지니어")  # 첫 링크는 직무만
        self.assertIn("클라우드 엔지니어 AWS", unquote(links[1]["url"]))
        self.assertEqual(len(communities.people_search_links("AI 엔지니어", [])), 1)  # 부족 기술이 없어도 직무 링크는 있다


if __name__ == "__main__":
    unittest.main()
