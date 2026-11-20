"""backend.api.get_recommendations 실제 모드의 개수 제한(RECOMMEND_TOP_N)과 캐시 확인.
실행: python -m unittest backend.test_recommend_api

연결 자체(수요 숫자를 코드로 만들어 ai.recommend 에 넘기기, ③ 형식)는 backend/test_api.py(#84)가 확인한다.
Bedrock 과 DynamoDB 는 부르지 않는다. ai.recommend.build_recommendation 과 get_demand 를 가짜로 바꿔 끼운다.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend import api

DEMAND = {
    "total_postings": 12,
    "skills": [
        {"skill": "AWS", "postings": 12, "required": 10, "preferred": 2},
        {"skill": "Docker", "postings": 6, "required": 4, "preferred": 2},
        {"skill": "CI/CD", "postings": 4, "required": 4, "preferred": 0},
        {"skill": "Kubernetes", "postings": 4, "required": 2, "preferred": 2},
        {"skill": "Network", "postings": 3, "required": 3, "preferred": 0},
    ],
}
GAP = ["AWS", "Docker", "CI/CD", "Kubernetes", "Network"]  # 화면은 수요 높은 순으로 준다


def fake_build(skill, demand):
    rec = {"gap_skill": skill, "demand": dict(demand), "courses": [
        {"title": f"{skill} 과정", "url": "https://example.com", "level": "기초",
         "prerequisites": "없음", "reason": "이유", "match": "certain"},
    ]}
    return rec, None


class RealRecommendationsTest(unittest.TestCase):
    def setUp(self) -> None:
        api._recommend_cache.clear()
        self.addCleanup(api._recommend_cache.clear)
        patch.dict(os.environ, {"BEDROCK_MOCK": "0"}).start()
        self.demand = patch.object(api, "get_demand", return_value=DEMAND).start()
        self.build = patch("ai.recommend.build_recommendation", side_effect=fake_build).start()
        self.addCleanup(patch.stopall)

    def test_only_top_n_are_recommended(self) -> None:
        recs = api.get_recommendations("cloud_engineer", GAP)
        self.assertEqual(api.RECOMMEND_TOP_N, 3)
        self.assertEqual([r["gap_skill"] for r in recs], ["AWS", "Docker", "CI/CD"])
        self.assertEqual([c.args[0] for c in self.build.call_args_list], ["AWS", "Docker", "CI/CD"])

    def test_demand_numbers_are_computed_from_get_demand(self) -> None:
        api.get_recommendations("cloud_engineer", GAP)
        given = {c.args[0]: c.args[1] for c in self.build.call_args_list}
        self.assertEqual(given["AWS"], {"postings": 12, "of": 12, "required_ratio": 0.83})
        self.assertEqual(given["CI/CD"], {"postings": 4, "of": 12, "required_ratio": 1.0})

    def test_top_n_constant_is_used(self) -> None:
        with patch.object(api, "RECOMMEND_TOP_N", 1):
            self.assertEqual([r["gap_skill"] for r in api.get_recommendations("cloud_engineer", GAP)], ["AWS"])

    def test_second_call_uses_cache(self) -> None:
        first = api.get_recommendations("cloud_engineer", GAP)
        first[0]["courses"].clear()  # 돌려받은 값을 바꿔도 캐시는 그대로
        second = api.get_recommendations("cloud_engineer", GAP)
        self.assertEqual(self.build.call_count, 3)
        self.assertEqual(len(second[0]["courses"]), 1)
        # 상위 3개가 같으면 뒤쪽 목록이 달라도 다시 부르지 않는다
        api.get_recommendations("cloud_engineer", GAP[:3])
        self.assertEqual(self.build.call_count, 3)

    def test_changed_demand_numbers_call_again(self) -> None:
        api.get_recommendations("cloud_engineer", GAP)
        changed = {**DEMAND, "total_postings": 13}
        self.demand.return_value = changed
        api.get_recommendations("cloud_engineer", GAP)
        self.assertEqual(self.build.call_count, 6)

    def test_changed_knowledge_base_does_not_reuse_recommendations(self) -> None:
        for kb in ("first", "second"):
            with patch.dict(os.environ, {"BEDROCK_KB_ID": kb}):
                api.get_recommendations("cloud_engineer", GAP)
        self.assertEqual(self.build.call_count, 6)

    def test_cache_keeps_only_recent_entries(self) -> None:
        with patch.object(api, "RECOMMEND_CACHE_MAX", 2):
            for total in (12, 13, 14):  # 수요 숫자가 달라 서로 다른 키 3개
                self.demand.return_value = {**DEMAND, "total_postings": total}
                api.get_recommendations("cloud_engineer", GAP)
            self.assertEqual(len(api._recommend_cache), 2)
            self.assertEqual(self.build.call_count, 9)
            api.get_recommendations("cloud_engineer", GAP)  # 14: 남아 있음 → 다시 안 부름
            self.assertEqual(self.build.call_count, 9)
            self.demand.return_value = DEMAND  # 12: 가장 오래돼 버려짐 → 다시 부름
            api.get_recommendations("cloud_engineer", GAP)
            self.assertEqual(self.build.call_count, 12)
            self.assertEqual(len(api._recommend_cache), 2)

    def test_failure_is_not_cached(self) -> None:
        self.build.side_effect = [RuntimeError("ThrottlingException")] + [fake_build(s, {}) for s in GAP[:3]]
        with self.assertRaises(RuntimeError):
            api.get_recommendations("cloud_engineer", GAP)
        self.assertEqual(len(api.get_recommendations("cloud_engineer", GAP)), 3)
        self.assertEqual(self.build.call_count, 4)

    def test_skill_missing_from_demand_is_passed_with_zero(self) -> None:
        # #84 와 같이 수요에 없는 기술도 건너뛰지 않고 0 으로 넘긴다. 상위 N 개에도 그대로 센다
        recs = api.get_recommendations("cloud_engineer", ["Foo", "AWS", "Docker", "CI/CD"])
        self.assertEqual([r["gap_skill"] for r in recs], ["Foo", "AWS", "Docker"])
        given = {c.args[0]: c.args[1] for c in self.build.call_args_list}
        self.assertEqual(given["Foo"], {"postings": 0, "of": 12, "required_ratio": 0})


class MockModeTest(unittest.TestCase):
    def test_mock_mode_is_unchanged(self) -> None:
        api._recommend_cache.clear()
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
            with patch("ai.recommend.build_recommendation") as build:
                recs = api.get_recommendations("cloud_engineer", GAP)
                build.assert_not_called()
        self.assertEqual([r["gap_skill"] for r in recs], GAP)  # 가짜 모드는 개수 제한 없이 그대로
        self.assertEqual(api._recommend_cache, {})


if __name__ == "__main__":
    unittest.main()
