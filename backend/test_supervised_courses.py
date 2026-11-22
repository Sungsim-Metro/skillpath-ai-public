"""명시한 학습 목표의 수요·과정 근거와 수준 적용을 확인한다."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import asdict
from unittest.mock import patch

from ai import bedrock, chat as intake, recommend, supervisor
from backend import api, chat

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


class RoleGapCourseTest(unittest.TestCase):
    switched = "이제 AI 엔지니어가 목표예요. "
    first_gap = "AI 직무에서 부족한 기술 중 첫 번째 기술의 강의를 보여줘"
    second_gap = "AI 기준 부족 기술 중 두 번째 강의를 보여줘."
    named_course = "AI 관점에서 첫 번째로 AI 강의를 보여줘."

    def setUp(self):
        self.enterContext(patch.dict(os.environ, BEDROCK_MOCK="1", USAGE_LOG="off"))

    def send(self, state, message):
        return chat.handle_message(state, message, job=JOB, sidebar_skills=[], architecture="supervised")

    def after_cloud_gap(self):
        state = chat.Conversation(job=JOB)
        self.send(state, "내 부족한 기술을 알려줘")
        self.assertEqual(state.last_gap_skills[0], "AWS")
        return state

    def test_new_role_ordinal_uses_its_comparable_gaps(self):
        for skills, request, target in [({}, self.first_gap, "Python"), ({"Python": True}, self.first_gap, "LLM"),
                                        ({}, self.second_gap, "LLM"), ({}, self.named_course, "AI"),
                                        ({}, "마지막으로 AI 강의 보여줘", "AI"),
                                        ({}, "마지막으로 부족 기술 중 두 번째 강의 보여줘", "LLM")]:
            with self.subTest(skills=skills, request=request):
                state = self.after_cloud_gap()
                state.skills.update(skills)
                with patch.object(api, "get_recommendations", wraps=api.get_recommendations) as lookup:
                    reply = self.send(state, self.switched + request)
                self.assertEqual(state.job, "ai_engineer")
                self.assertEqual(lookup.call_args.args, ("ai_engineer", [target]), reply.text)

    def test_approved_model_cannot_use_old_target_or_lose_new_explicit_gap_scope(self):
        interpret = supervisor.interpret_turn
        own_goal = self.switched + self.first_gap
        cases = [
            (own_goal, "ai_engineer", "targets", ["AWS"], "ai_engineer", "Python"),
            (own_goal, None, "targets", ["AWS"], "ai_engineer", "Python"),
            (own_goal, "ai_engineer", "clarify", [], "ai_engineer", "Python"),
            (self.switched + self.second_gap, "ai_engineer", "targets", ["AI"], "ai_engineer", "LLM"),
            (self.switched + self.named_course, "ai_engineer", "gap", [], "ai_engineer", "AI"),
            ("친구가 AI 엔지니어가 목표예요. 부족 기술 중 첫 번째 강의를 보여줘",
             "ai_engineer", "targets", ["AWS"], JOB, "AWS"),
        ]
        for message, planned_job, scope, names, job, target in cases:
            with self.subTest(message=message, planned_job=planned_job, scope=scope):
                state = self.after_cloud_gap()
                candidate = {"turn": asdict(intake.Turn(job=planned_job, action="courses")),
                             "add_wishes": [], "remove_wishes": [], "course_scope": scope, "targets": names}
                outputs = [(json.dumps(obj), "end_turn", {}) for obj in
                           (candidate, {"decision": "continue", "issue": "none"})]

                def live_plan(message, context):
                    with patch.object(bedrock, "is_mock", return_value=False), \
                            patch.object(bedrock, "_call", side_effect=outputs):
                        return interpret(message, context)

                with patch.object(supervisor, "interpret_turn", side_effect=live_plan), \
                        patch.object(supervisor, "approve_result", wraps=supervisor.approve_result) as verify:
                    reply = self.send(state, message)
                self.assertEqual(state.job, job)
                context, plan = verify.call_args.args[1:3]
                self.assertEqual(context["job"], job)
                self.assertEqual(context["last_gap_skills"], [] if job != JOB else state.last_gap_skills)
                self.assertEqual(plan.targets, [target], reply.text)
                self.assertIn(target, reply.text)

    def test_unseen_or_out_of_range_reference_keeps_new_role_and_asks(self):
        for request in ("그중 첫 번째 기술의 강의를 보여줘", "부족한 기술 중 999번째 강의를 보여줘"):
            with self.subTest(request=request):
                state = self.after_cloud_gap()
                reply = self.send(state, self.switched + request)
                self.assertEqual(state.job, "ai_engineer")
                self.assertEqual(reply.action, "clarify", reply.text)
                self.assertEqual(reply.courses, [])

    def test_ordinal_keeps_same_role_order_and_explicit_topic(self):
        for message, job, target in [
            ("팀원이 AI 엔지니어가 목표예요. 그중 첫 번째 기술의 강의를 보여줘", JOB, "AWS"),
            (self.switched + "첫 번째로 Docker 강의를 보여줘", "ai_engineer", "Docker"),
        ]:
            with self.subTest(message=message):
                state = self.after_cloud_gap()
                with patch.object(api, "get_recommendations", wraps=api.get_recommendations) as lookup:
                    reply = self.send(state, message)
                self.assertEqual(state.job, job)
                self.assertEqual(lookup.call_args.args, (job, [target]), reply.text)


class SupervisedCoursesTest(unittest.TestCase):
    def setUp(self):
        api._recommend_cache.clear()
        self.addCleanup(api._recommend_cache.clear)
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "USAGE_LOG": "off"}))
        self.demand = self.enterContext(patch.object(api, "get_demand", return_value=DEMAND))
        self.search = self.enterContext(patch.object(recommend, "candidate_courses", side_effect=catalog))
        self.model = self.enterContext(patch.object(bedrock, "_call", side_effect=AssertionError("offline")))

    def answer(self, targets, levels=None, facts=None, **kwargs):
        from backend.supervised_courses import recommend_for_targets

        return recommend_for_targets(JOB, targets, levels or {}, facts or FACTS, **kwargs)

    def test_explicit_difficulty_is_independent_of_unrated_and_corrected_skills(self):
        def build(skill, demand, *, review, course_levels):
            self.assertTrue(review)
            self.assertEqual(course_levels, ("고급",))
            card = dict(catalog(skill)[0], level="고급", reason="검토된 목표")
            return {"gap_skill": skill, "demand": demand, "courses": [card]}, None

        ratings = {"custom:Kubernetes": 2}
        facts = {"skills": ["도커"], "excluded_skills": ["k8s"]}
        with patch.object(recommend, "build_recommendation", side_effect=build):
            reply = self.answer(["Docker", "Kubernetes"], ratings, facts, course_levels=("고급",))
        self.assertEqual([(c["gap_skill"], c["level"]) for c in reply.courses],
                         [("Docker", "고급"), ("Kubernetes", "고급")])
        self.assertNotIn("내 수준", reply.text)
        self.assertEqual(ratings, {"custom:Kubernetes": 2})

    def test_empty_requested_level_is_distinct_from_a_retrieval_failure(self):
        def build(skill, demand, **kwargs):
            if skill == "Docker":
                raise RuntimeError("offline")
            return {"gap_skill": skill, "demand": demand, "courses": []}, None

        with patch.object(recommend, "build_recommendation", side_effect=build):
            reply = self.answer(["GraphQL", "Docker"], course_levels=("고급",))
        lines = reply.text.splitlines()
        self.assertIn("GraphQL", lines[0])
        self.assertIn("고급", lines[0])
        self.assertIn("찾지 못", lines[0])
        self.assertIn("불러오지 못", lines[1])
        self.assertTrue(reply.failed)

    def test_explicit_targets_compare_only_when_every_target_has_demand_evidence(self):
        pair = ("single", "reviewed")
        card = {**catalog("Linux")[0], "gap_skill": "Linux", "reason": "검토"}
        options = [{"structure": side, "courses": [card], "tokens": {}} for side in pair]
        with patch("backend.chat._compare_structures", return_value=(options, [card], [])) as compare:
            first = self.answer(["Linux", "Docker"], course_levels=("중급",), pair=pair)
            self.assertEqual(compare.call_args.args[0], pair)
            self.assertEqual(list(compare.call_args.args[3]), ["Linux", "Docker"])
            self.assertEqual(compare.call_args.kwargs["course_levels"], ("중급",))
            self.assertEqual(first.alternatives, options)
            self.assertTrue(first.courses)
            self.assertIn("중급", first.text)
            both = self.answer(["Linux", "Kubernetes"], pair=pair)
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(both.alternatives, [])
        self.assertEqual({c["gap_skill"] for c in both.courses}, {"Linux", "Kubernetes"})

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
        draft = {"reasons": [{"course_id": cid, "reason": "모든 공고에서 요구합니다.", "objective_id": "O1"}]}
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
