"""채팅 전용 추천에서 실제 모델 요청 수와 최종 근거 검사를 확인한다."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import bedrock, recommend
from backend import api


COURSE = {
    "course_id": "CATALOG1", "title": "Docker Basics", "level": "기초",
    "url": "https://skillbuilder.aws/example", "prerequisites": "표기 없음",
    "objectives": ["Explain Docker containers."], "subskill_id": "C1", "match": "certain",
}
DEMAND = {"postings": 8, "of": 12, "required_ratio": 0.25}
OBJECTIVE = COURSE["objectives"][0]
DRAFT = {"reasons": [{
    "course_id": "CATALOG1", "reason": "Docker 컨테이너를 설명하는 과정입니다.", "objective_id": "O1",
}]}


def verdict(decision: str) -> dict:
    return {"reviews": [{
        "course_id": "CATALOG1", "decision": decision, "feedback": "근거를 확인하세요.",
        "supported_objective_ids": ["O1"],
    }]}


def model_response(data) -> tuple[str, str, dict]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return text, "end_turn", {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3}


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "0", "USAGE_LOG": "off"}))
        self.search = self.enterContext(patch.object(recommend, "candidate_courses", return_value=[COURSE]))
        self.model = self.enterContext(patch.object(bedrock, "_call"))

    def build(self, *responses):
        self.model.side_effect = [model_response(response) for response in responses]
        return recommend.build_recommendation("Docker", DEMAND, review=True)

    def test_approved_draft_requires_independent_review_and_exact_objective(self):
        rec, result = self.build(DRAFT, verdict("approve"))
        self.assertEqual(self.model.call_count, 2)
        self.assertNotEqual(self.model.call_args_list[0].args[0], self.model.call_args_list[1].args[0])
        self.assertIn(DRAFT["reasons"][0]["reason"], rec["courses"][0]["reason"])
        self.assertIn(OBJECTIVE, rec["courses"][0]["reason"])
        self.assertEqual(result.usage["totalTokens"], 6)
        self.assertEqual(set(rec["courses"][0]), {
            "title", "url", "level", "prerequisites", "reason", "match",
        })

    def test_draft_and_review_each_retry_a_malformed_response_once(self):
        rec, result = self.build("invalid", DRAFT, {"reviews": []}, verdict("approve"))
        self.assertEqual(self.model.call_count, 4)
        self.assertEqual(result.usage["totalTokens"], 12)
        self.assertTrue(rec["courses"])

    def test_repair_uses_only_an_exact_objective_and_source_demand(self):
        unsupported = {"reasons": [{**DRAFT["reasons"][0], "reason": "이 과정이 취업을 보장합니다."}]}
        fixed = {"corrections": [{"course_id": "CATALOG1", "objective_id": "O1"}]}
        rec, result = self.build(unsupported, verdict("revise"), fixed)
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(result.usage["totalTokens"], 9)
        reason = rec["courses"][0]["reason"]
        self.assertIn("8건", reason)
        self.assertIn(OBJECTIVE, reason)
        self.assertNotIn("보장", reason)

    def test_bad_correction_is_never_retried_or_shown(self):
        for correction in ({"corrections": [{"course_id": "CATALOG1", "objective_id": "O999"}]},
                           "not valid JSON"):
            with self.subTest(correction=correction):
                self.model.reset_mock()
                with self.assertRaises((bedrock.BedrockJSONError, ValueError)):
                    self.build(DRAFT, verdict("revise"), correction)
                self.assertEqual(self.model.call_count, 3)

    def test_correction_must_use_an_objective_the_reviewer_found_relevant(self):
        self.search.return_value = [{**COURSE, "objectives": [OBJECTIVE, "Switch accounts."]}]
        with self.assertRaises(ValueError):
            self.build(DRAFT, verdict("revise"), {
                "corrections": [{"course_id": "CATALOG1", "objective_id": "O2"}],
            })
        self.assertEqual(self.model.call_count, 3)

    def test_approval_cannot_override_unknown_course_or_objective_id(self):
        for change in ({"objective_id": "O999"}, {"course_id": "invented"}):
            with self.subTest(change=change):
                draft = {"reasons": [{**DRAFT["reasons"][0], **change}]}
                with self.assertRaises((bedrock.BedrockJSONError, ValueError)):
                    self.build(draft, verdict("approve"))

    def test_invalid_prose_uses_only_the_approved_catalog_objective_and_source_demand(self):
        for reason in ("구직자 500명에게 추천합니다.", "Azure 실습 과정입니다.", "Learn containers."):
            with self.subTest(reason=reason):
                draft = {"reasons": [{**DRAFT["reasons"][0], "reason": reason}]}
                rec, _ = self.build(draft, verdict("approve"))
                shown = rec["courses"][0]["reason"]
                self.assertIn(OBJECTIVE, shown)
                self.assertIn("12건 중 8건", shown)
                self.assertNotIn(reason, shown)

    def test_broken_correction_preserves_approved_course_and_suppresses_revised_draft(self):
        self.search.return_value = [COURSE, {**COURSE, "course_id": "CATALOG2", "title": "다른 과정"}]
        first = DRAFT["reasons"][0]
        second = {**first, "course_id": "CATALOG2"}
        approved = verdict("approve")["reviews"][0]
        revise = {**approved, "course_id": "CATALOG2", "decision": "revise"}
        for correction in ("not JSON", {"corrections": [{**second, "objective_id": "O404"}]}):
            with self.subTest(correction=correction):
                rec, result = self.build({"reasons": [first, second]}, {"reviews": [approved, revise]}, correction)
                self.assertEqual([row["title"] for row in rec["courses"]], [COURSE["title"]])
                self.assertTrue(result.data["incomplete_review"])
                self.assertEqual(result.usage["totalTokens"], 9)

    def test_empty_search_and_failed_review_produce_different_outcomes(self):
        self.search.return_value = []
        rec, result = self.build()
        self.assertEqual(rec["courses"], [])
        self.assertIsNone(result)
        self.model.assert_not_called()
        self.search.return_value = [COURSE]
        with self.assertRaises(ValueError):
            self.build(DRAFT, verdict("reject"))
        self.assertEqual(self.model.call_count, 2)

    def test_rejected_course_is_removed_while_approved_course_is_shown(self):
        self.search.return_value = [COURSE, {**COURSE, "course_id": "CATALOG2", "title": "다른 과정"}]
        first = DRAFT["reasons"][0]
        approved = verdict("approve")["reviews"][0]
        rec, _ = self.build(
            {"reasons": [first, {**first, "course_id": "CATALOG2"}]},
            {"reviews": [approved, {**approved, "course_id": "CATALOG2", "decision": "reject"}]},
        )
        self.assertEqual([row["title"] for row in rec["courses"]], [COURSE["title"]])

    def test_invalid_retry_setting_fails_before_any_model_call(self):
        with self.assertRaises(ValueError):
            bedrock.converse_json("", "", mock_file="", mock_key="", max_attempts=0)
        self.model.assert_not_called()


class ChatRecommendationApiTest(unittest.TestCase):
    def setUp(self):
        api._recommend_cache.clear()
        self.addCleanup(api._recommend_cache.clear)

    @unittest.skipUnless(recommend.COURSES_CSV.is_file(), "로컬 과정 카탈로그가 없는 공개 배포본")
    def test_mock_uses_catalog_objectives_and_dashboard_keeps_its_fixture(self):
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), patch.object(bedrock, "_call") as model:
            original = api.get_recommendations("cloud_engineer", ["Docker"])[0]
            reviewed = api.get_recommendations("cloud_engineer", ["Docker"], review=True)[0]
            self.assertEqual(len(reviewed["courses"]), 3)
            self.assertNotEqual(original["courses"][0]["url"], reviewed["courses"][0]["url"])
            source = {row["url"]: row for row in recommend.candidate_courses("Docker")}
            for row in reviewed["courses"]:
                self.assertTrue(any(o in row["reason"] for o in source[row["url"]]["objectives"]))
            model.assert_not_called()

    def test_missing_catalog_uses_only_labelled_public_mock_courses(self):
        with tempfile.TemporaryDirectory() as empty, patch.object(
            recommend, "COURSES_CSV", Path(empty) / "courses.csv",
        ):
            with (patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), patch.object(bedrock, "_call") as model,
                  patch.object(recommend, "search_courses") as search):
                rows = api.get_recommendations("cloud_engineer", ["Docker", "Git"], review=True)
                self.assertFalse(rows[1]["courses"])
                course = rows[0]["courses"][0]
                self.assertTrue(course["title"].startswith("가상 과정"))
                source = recommend.candidate_courses("Docker", rules_only=True)[0]
                self.assertIn(source["objectives"][0], course["reason"])
                self.assertIn("/data/sample/chat_courses.json", course["url"])
                model.assert_not_called()
                search.assert_not_called()
            with (patch.dict(os.environ, {"BEDROCK_MOCK": "0"}),
                  patch.object(recommend, "search_courses", side_effect=FileNotFoundError)):
                with self.assertRaises(FileNotFoundError):
                    recommend.build_recommendation("Docker", DEMAND, review=True)

    def test_incomplete_review_is_retried_on_next_request(self):
        second = {**COURSE, "course_id": "CATALOG2", "title": "다른 과정"}
        draft = {"reasons": [DRAFT["reasons"][0], {**DRAFT["reasons"][0], "course_id": "CATALOG2"}]}
        approved = verdict("approve")["reviews"][0]
        revise = {**approved, "course_id": "CATALOG2", "decision": "revise"}
        reviews = {"reviews": [approved, revise]}
        fixed = {"corrections": [{"course_id": "CATALOG2", "objective_id": "O1"}]}
        responses = (draft, reviews, "invalid", draft, reviews, fixed)
        with (patch.dict(os.environ, {"BEDROCK_MOCK": "0", "USAGE_LOG": "off"}),
              patch.object(recommend, "candidate_courses", return_value=[COURSE, second]),
              patch.object(bedrock, "_call", side_effect=[model_response(r) for r in responses])):
            def request():
                return api.get_recommendations("ai_engineer", ["Docker"], review=True,
                                               review_evidence={"Docker": DEMAND})[0]["courses"]

            self.assertEqual([r["title"] for r in request()], [COURSE["title"]])
            self.assertEqual([r["title"] for r in request()], [COURSE["title"], second["title"]])

    def test_dashboard_cache_cannot_bypass_review(self):
        demand = {"total_postings": 12, "skills": [{"skill": "Docker", "postings": 8, "required": 2}]}
        with (patch.dict(os.environ, {"BEDROCK_MOCK": "0"}), patch.object(api, "get_demand", return_value=demand),
              patch.object(recommend, "candidate_courses", return_value=[COURSE]),
              patch.object(bedrock, "_call", side_effect=[
                  model_response(DRAFT), model_response(DRAFT), model_response(verdict("approve")),
              ]) as model):
            api.get_recommendations("cloud_engineer", ["Docker"])
            self.assertEqual(model.call_count, 1)
            api.get_recommendations("cloud_engineer", ["Docker"], review=True)
            api.get_recommendations("cloud_engineer", ["Docker"], review=True)
            self.assertEqual(model.call_count, 3)

    def test_other_skills_survive_a_rejected_skill_and_partial_result_is_not_cached(self):
        demand = {
            "total_postings": 12, "skills": [
                {"skill": s, "postings": 8, "required": 2} for s in ("Docker", "Linux")
            ],
        }
        with (patch.dict(os.environ, {"BEDROCK_MOCK": "0"}),
              patch.object(api, "get_demand", return_value=demand),
              patch.object(recommend, "candidate_courses", return_value=[COURSE]),
              patch.object(bedrock, "_call", side_effect=[
                  model_response(DRAFT), model_response(verdict("reject")),
                  model_response(DRAFT), model_response(verdict("approve")),
              ])):
            rows = api.get_recommendations("cloud_engineer", ["Docker", "Linux"], review=True)
        self.assertEqual([r["gap_skill"] for r in rows], ["Linux"])
        self.assertTrue(rows[0]["courses"])
        self.assertFalse(api._recommend_cache)

    def test_transient_error_preserves_other_verified_skills_and_propagates_if_alone(self):
        demand = {"total_postings": 12, "skills": []}
        accepted = {"gap_skill": "Docker", "demand": DEMAND, "courses": [COURSE]}
        offline = RuntimeError("unavailable")
        with (patch.dict(os.environ, {"BEDROCK_MOCK": "0"}),
              patch.object(api, "get_demand", return_value=demand),
              patch.object(recommend, "build_recommendation", side_effect=[
                  (accepted, None), offline, offline, ValueError("invalid"),
              ])):
            rows = api.get_recommendations("cloud_engineer", ["Docker", "Linux"], review=True)
            self.assertEqual(rows, [accepted])
            self.assertFalse(api._recommend_cache)
            with self.assertRaises(RuntimeError) as failure:
                api.get_recommendations("cloud_engineer", ["Linux", "Docker"], review=True)
            self.assertIs(failure.exception, offline)
            self.assertFalse(api._recommend_cache)
