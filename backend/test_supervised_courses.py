"""명시한 학습 목표의 수요·과정 근거와 수준 적용을 확인한다."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from ai import bedrock, recommend
from backend import api

JOB = "cloud_engineer"
FACTS = {"skills": [], "excluded_skills": []}
DEMAND = {
    "total_postings": 40,
    "skills": [
        {"skill": "Docker", "postings": 1, "required": 1, "preferred": 0},
        {"skill": "Linux", "postings": 32, "required": 20, "preferred": 12},
    ],
}


def catalog(skill: str, **kwargs) -> list[dict]:
    if skill not in {"Docker", "Linux", "Kubernetes"}:
        return []
    return [
        {
            "course_id": f"{skill}-{i}", "title": f"{skill} {level}",
            "url": f"https://skillbuilder.aws/courses/{skill}-{i}", "level": level,
            "prerequisites": "없음", "objectives": [f"{skill} 환경을 구성한다."],
            "match": "certain", "subskill_id": "C1-4", "rating_avg": 4.5, "rating_count": 12,
        }
        for i, level in enumerate(("중급", "기초"))
    ]


class SupervisedCoursesTest(unittest.TestCase):
    def setUp(self):
        api._recommend_cache.clear()
        self.addCleanup(api._recommend_cache.clear)
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "USAGE_LOG": "off"}))
        self.demand = self.enterContext(patch.object(api, "get_demand", return_value=DEMAND))
        self.search = self.enterContext(patch.object(recommend, "candidate_courses", side_effect=catalog))
        self.model = self.enterContext(patch.object(bedrock, "_call", side_effect=AssertionError("offline")))

    def answer(self, targets, levels=None, facts=None):
        from backend.supervised_courses import recommend_for_targets

        return recommend_for_targets(JOB, targets, levels or {}, facts or FACTS)

    def test_absent_observation_cannot_reuse_legacy_zero_or_invent_demand_in_reason(self):
        legacy = api.get_recommendations(JOB, ["Kubernetes"], review=True)
        self.assertIn("0건", legacy[0]["courses"][0]["reason"])
        observed = api.get_recommendations(JOB, ["Kubernetes"], review=True, review_evidence={})
        self.assertIsNone(observed[0]["demand"])
        reason = observed[0]["courses"][0]["reason"]
        self.assertIn("Kubernetes 환경을 구성한다.", reason)
        self.assertNotIn("0건", reason)
        self.assertNotIn("공고", reason)
        self.assertEqual(len(api._recommend_cache), 2)
        self.model.assert_not_called()

    def test_absent_demand_live_review_uses_only_reviewed_objective(self):
        course = catalog("Docker")[0]
        cid, objective = course["course_id"], course["objectives"][0]
        draft = {"reasons": [{"course_id": cid, "reason": "모든 공고에서 요구합니다.", "objective": objective}]}
        verdict = {"reviews": [{"course_id": cid, "decision": "approve", "feedback": ""}]}
        self.search.side_effect = None
        self.search.return_value = [course]
        self.model.side_effect = [(json.dumps(row), "end_turn", {}) for row in (draft, verdict)]
        with patch.dict(os.environ, {"BEDROCK_MOCK": "0"}):
            rows = api.get_recommendations(JOB, ["Docker"], review=True, review_evidence={})
        self.assertEqual(self.model.call_count, 2)
        self.assertIn(objective, rows[0]["courses"][0]["reason"])
        self.assertNotIn("공고", rows[0]["courses"][0]["reason"])

    def test_requested_order_alias_low_demand_and_per_skill_rating(self):
        reply = self.answer(["도커", "Docker", "Linux", "k8s"],
                            {"custom:Docker": 0, "custom:Linux": 4})
        self.assertEqual([c["gap_skill"] for c in reply.courses],
                         ["Docker", "Docker", "Linux", "Linux", "Kubernetes", "Kubernetes"])
        self.assertEqual([c["level"] for c in reply.courses], ["기초", "중급", "중급", "기초", "중급", "기초"])
        first = reply.courses[0]
        self.assertIn("40건 중 1건", first["reason"])
        self.assertIn("Docker 환경을 구성한다.", first["reason"])
        self.assertEqual((first["url"], first["match"], first["rating_count"]),
                         ("https://skillbuilder.aws/courses/Docker-1", "certain", 12))
        self.assertIn("수요", reply.text)
        self.assertIn("Kubernetes", reply.text)
        self.assertFalse(reply.failed)
        self.model.assert_not_called()

    def test_owned_unrated_or_conflicting_skill_is_deferred_but_explicit_zero_is_eligible(self):
        facts = {"skills": ["도커"], "excluded_skills": ["k8s"]}
        deferred = self.answer(["Docker", "Kubernetes"], {"custom:Kubernetes": 2}, facts)
        self.assertFalse(deferred.courses)
        self.assertIn("수준 미입력", deferred.text)
        self.assertIn("Docker", deferred.text)
        self.assertIn("보류", deferred.text)
        self.assertIn("Kubernetes", deferred.text)
        self.search.assert_not_called()
        reply = self.answer(["도커"], {"custom:Docker": 0}, facts)
        self.assertEqual(reply.courses[0]["level"], "기초")

    def test_no_demand_keeps_verified_catalog_result_with_no_posting_claim(self):
        self.demand.return_value = {"total_postings": 0, "skills": []}
        reply = self.answer(["도커"])
        self.assertTrue(reply.courses)
        self.assertIn("수요", reply.text)
        self.assertNotIn("0건", reply.text + reply.courses[0]["reason"])
        self.assertIn("Docker 환경을 구성한다.", reply.courses[0]["reason"])
        self.assertFalse(reply.failed)

    def test_demand_store_error_still_allows_catalog_evidence_and_reports_read_failure(self):
        self.demand.side_effect = RuntimeError("internal details")
        reply = self.answer(["Linux"])
        self.assertTrue(reply.courses)
        self.assertIn("불러오지 못", reply.text)
        self.assertNotIn("internal details", reply.text)
        self.assertNotIn("공고", reply.courses[0]["reason"])

    def test_no_match_names_requested_skill_without_falling_back_to_other_courses(self):
        reply = self.answer(["GraphQL"])
        self.assertFalse(reply.courses)
        self.assertFalse(reply.failed)
        self.assertIn("GraphQL", reply.text)
        self.assertIn("찾지 못", reply.text)
        self.assertNotIn("Linux", reply.text)

    def test_bad_card_does_not_hide_valid_skill_and_is_distinct_from_no_match(self):
        def bad_link(skill, **kwargs):
            rows = catalog(skill)
            if skill == "Docker":
                rows[0]["url"] = "http://broken.invalid/"
            return rows

        self.search.side_effect = bad_link
        reply = self.answer(["Docker", "Linux", "GraphQL"])
        self.assertEqual([c["gap_skill"] for c in reply.courses], ["Linux", "Linux"])
        self.assertTrue(reply.failed)
        self.assertIn("검증", reply.text)
        self.assertIn("찾지 못", reply.text)
        self.assertIn("GraphQL", reply.text)

    def test_all_failed_and_over_limit_are_explained_per_skill(self):
        self.search.side_effect = RuntimeError("internal details")
        reply = self.answer(["Docker", "Linux", "GraphQL", "Kubernetes"])
        self.assertFalse(reply.courses)
        self.assertTrue(reply.failed)
        self.assertIn("불러오지 못", reply.text)
        self.assertIn("Kubernetes", reply.text)
        self.assertIn("3", reply.text)
        self.assertNotIn("internal details", reply.text)
        self.assertEqual(self.search.call_count, 3)


if __name__ == "__main__":
    unittest.main()
