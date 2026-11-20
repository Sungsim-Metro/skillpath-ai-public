"""Offline tests for supervised routing, corrections and the two independent reviews."""
import json
import os
import unittest
from dataclasses import asdict
from unittest.mock import patch

from ai import chat


class SupervisedLearningTest(unittest.TestCase):
    def setUp(self):
        from ai import supervisor
        self.supervisor = supervisor
        mode = patch.dict(os.environ, BEDROCK_MOCK="1", USAGE_LOG="off")
        mode.start()
        self.addCleanup(mode.stop)

    def test_candidate_validation_prunes_like_the_default_structure(self):
        """감독형 구조도 기본 구조처럼 어긋난 항목만 버린다(AWS S3 → S3, 말하지 않은 기술은 삭제)."""
        message = "S3 버킷을 직접 만들어 봤어요."
        turn = asdict(chat.Turn(status="ok", add_skills=["AWS S3", "Terraform"]))
        candidate = {"turn": turn, "add_wishes": [], "remove_wishes": [], "targets": [], "course_scope": "none"}
        self.supervisor._validate_candidate(candidate, message, {})
        self.assertEqual(candidate["turn"]["add_skills"], ["S3"])

    def test_wish_is_separate_from_own_and_teammates_completed_work(self):
        raw = "팀원이 Docker를 썼고 저는 Python으로 API를 개발했어요. Terraform을 배우고 싶어요."
        with patch("ai.bedrock._client", side_effect=AssertionError):
            plan = self.supervisor.interpret_turn(raw, {})
            self.assertEqual(plan.turn.add_skills, ["Python"])
            self.assertEqual(plan.wishes, ["Terraform"])
            self.assertEqual(plan.targets, [])
            self.assertEqual(plan.turn.action, "note")
            follow = self.supervisor.interpret_turn("그 기술 강의 추천해줘", {"wishes": plan.wishes})
            self.assertEqual(follow.turn.action, "courses")
            self.assertEqual(follow.targets, ["Terraform"])
            self.assertFalse(follow.turn.save)

    def test_explicit_replacement_corrects_wish_and_preserves_unrelated_wish(self):
        context = {"wishes": ["Git", "Docker"]}
        new = self.supervisor.interpret_turn("Docker 말고 쿠버네티스를 배우고 싶어요.", context)
        self.assertEqual(new.wishes, ["Git", "Kubernetes"])
        direct = self.supervisor.interpret_turn("Podman 강의를 추천해줘", {"wishes": new.wishes})
        self.assertEqual(direct.targets, ["Podman"])
        self.assertEqual(direct.wishes, new.wishes)
        self.assertFalse(direct.turn.add_skills)

    def test_interests_from_other_people_and_negative_wishes_are_not_added(self):
        for raw in ["친구는 Docker를 배우고 싶대요", "Docker를 공부하고 싶지 않아요"]:
            with self.subTest(raw=raw):
                plan = self.supervisor.interpret_turn(raw, {})
                self.assertEqual(plan.wishes, [])
                self.assertEqual(plan.turn.add_skills, [])

    def test_ambiguous_reference_yields_question_and_next_answer_restores_course_request(self):
        context = {"wishes": ["Docker", "Kubernetes"]}
        for wishes in [[], context["wishes"]]:
            with self.subTest(wishes=wishes):
                query = self.supervisor.interpret_turn("그 기술 강의 추천해줘", {"wishes": wishes})
                self.assertTrue(query.question)
                self.assertEqual(query.targets, [])
                self.assertFalse(query.turn.save)
        continued = self.supervisor.interpret_turn("쿠버네티스요", dict(context, pending_course_question=True))
        self.assertEqual(continued.turn.action, "courses")
        self.assertEqual(continued.targets, ["Kubernetes"])

    def test_direct_new_scope_overrides_learning_wishes(self):
        context = {"wishes": ["Docker"]}
        for raw, targets in [("강의 추천해줘", ["Docker"]), ("부족 기술 기준으로 강의 추천해줘", []),
                             ("Terraform 강의 추천해줘", ["Terraform"])]:
            with self.subTest(raw=raw):
                plan = self.supervisor.interpret_turn(raw, context)
                self.assertFalse(plan.question)
                self.assertEqual(plan.targets, targets)

    def test_reporting_a_target_by_name_does_not_claim_personal_use(self):
        plan = self.supervisor.interpret_turn("S3를 뜻했어요. S3 강의를 추천해 주세요.", {})
        self.assertEqual(plan.targets, ["S3"])
        self.assertEqual(plan.turn.add_skills, [])

    def test_reattribution_retracts_earlier_ownership_only_on_correction(self):
        state = {"skills": ["Git", "Python"], "experiences": ["제가 Git으로 배포했어요"]}
        note = self.supervisor.interpret_turn("동료가 Git을 사용했어요.", state)
        self.assertEqual(note.turn.remove_skills, [])
        revised = self.supervisor.interpret_turn("정정할게요. Git은 동료가 사용했고 저는 Python만 썼어요.", state)
        self.assertEqual(revised.turn.remove_skills, ["Git"])
        self.assertEqual(revised.turn.remove_experiences, state["experiences"])

    def test_earlier_wish_reference_survives_quoted_clause_boundary(self):
        plan = self.supervisor.interpret_turn("앞서 배우고 싶다고 말씀드린 기술의 강의 보여주세요.",
                                               {"wishes": ["S3"]})
        self.assertEqual(plan.targets, ["S3"])

    def test_wrong_subject_course_card_is_rejected_even_when_title_mentions_target(self):
        plan = self.supervisor.interpret_turn("Docker 강의 추천해줘", {})
        self.assertFalse(self.supervisor.approve_result("Docker 강의 추천해줘", {}, plan, "강의 추천",
                                                        [{"gap_skill": "Git", "title": "Docker 소개"}]))

    def test_gap_list_reference_uses_actual_display_order_and_checks_bounds(self):
        context = {"wishes": ["Docker"], "last_gap_skills": ["IAM", "S3"]}
        for text, expected in [("부족 기술 중 첫 번째 과정 추천해줘", ["IAM"]),
                               ("그중 두 번째 강의 찾아줘", ["S3"])]:
            self.assertEqual(self.supervisor.interpret_turn(text, context).targets, expected)
        self.assertTrue(self.supervisor.interpret_turn("그중 세 번째 강의 찾아줘", context).question)
        self.assertTrue(self.supervisor.interpret_turn("첫 번째 기술 강의 추천해줘", {}).question)
        self.assertEqual(self.supervisor.interpret_turn("네", {"offered_gap": True}).turn.action, "gap")

    def test_mock_preserves_each_explicit_profile_request_in_the_same_message(self):
        context = {"skills": ["Git", "Docker"], "experiences": ["제가 Git으로 앱을 배포했어요"]}
        plan = self.supervisor.interpret_turn("Git 경험을 저장해줘. 그리고 Docker를 저장해줘.", context)
        self.assertEqual(plan.turn.save_skills, ["Docker"])
        self.assertEqual(plan.turn.save_experiences, context["experiences"])


class RealSupervisorBoundaryTest(unittest.TestCase):
    def setUp(self):
        from ai import supervisor
        self.supervisor = supervisor
        mode = patch.dict(os.environ, BEDROCK_MOCK="0", BEDROCK_MODEL_ID="test", USAGE_LOG="off")
        mode.start()
        self.addCleanup(mode.stop)
        client = patch("ai.bedrock._client")
        self.converse = client.start().return_value.converse
        self.addCleanup(client.stop)

    @staticmethod
    def intake(action="courses", targets=(), scope="targets", **kwargs):
        return {"turn": asdict(chat.Turn(action=action, **kwargs)), "add_wishes": [],
                "remove_wishes": [], "targets": list(targets), "course_scope": scope}

    def respond(self, *payloads):
        self.converse.side_effect = [
            {"output": {"message": {"content": [{"text": json.dumps(data, ensure_ascii=False)}]}}}
            for data in payloads
        ]

    def requests(self):
        return [json.loads(call.kwargs["messages"][0]["content"][0]["text"])
                for call in self.converse.call_args_list]

    def test_manager_uses_original_message_and_repairs_wrong_course_scope_once(self):
        raw = "  그 기술 강의 추천해줘  "
        prior = {"wishes": ["Docker"], "history": [{"role": "user", "content": "도커를 배우고 싶어요."}]}
        self.respond(self.intake(scope="gap"), {"decision": "reinterpret", "issue": "course_target", "quote": "그 기술"},
                     self.intake(targets=["Docker"]), {"decision": "continue", "issue": "none"})
        plan = self.supervisor.interpret_turn(raw, prior)
        self.assertEqual(plan.targets, ["Docker"])
        self.assertEqual(plan.decisions, ["reinterpret", "continue"])
        first, review, retry, second_review = self.requests()
        for request in (first, review, retry, second_review):
            self.assertEqual(request["message"], raw)
            self.assertEqual(request["context"]["wishes"], ["Docker"])
        self.assertEqual(review["candidate"]["targets"], [])
        self.assertTrue(retry["feedback"])
        self.assertEqual(retry["feedback"]["quote"], "그 기술")
        self.assertEqual(second_review["candidate"]["targets"], ["Docker"])

    def test_second_rejection_and_clarification_discard_writes_and_proposals(self):
        raw = "Docker만 저장해줘"
        context = {"skills": ["Docker"], "wishes": ["Git"]}
        proposed = self.intake("note", scope="none", save=True, save_skills=["Docker"])
        retry = {"decision": "reinterpret", "issue": "profile_scope", "quote": "Docker만"}
        self.respond(proposed, retry, proposed, retry)
        plan = self.supervisor.interpret_turn(raw, context)
        self.assertTrue(plan.question)
        self.assertFalse(plan.turn.save)
        self.assertEqual(plan.wishes, ["Git"])
        self.assertEqual(plan.decisions[-1], "clarify")
        self.assertEqual(self.converse.call_count, 4)

    def test_ungrounded_target_fails_safely_and_cannot_be_forwarded_to_workers(self):
        invented = self.intake(targets=["secret-hallucination"])
        self.respond(invented, invented)
        with self.assertRaises(chat.ChatIntakeError) as caught:
            self.supervisor.interpret_turn("Docker 강의를 보여줘", {})
        self.assertNotIn("secret-hallucination", str(caught.exception))
        self.assertEqual(self.converse.call_count, 2)

    def test_semantic_result_review_excludes_urls_ratings_and_private_context(self):
        plan = self.supervisor.SupervisedTurn(chat.Turn(action="courses"), targets=["Docker"])
        courses = [{"gap_skill": "Docker", "title": "컨테이너 입문", "reason": "컨테이너 학습",
                    "match": "certain", "url": "https://private.invalid/", "rating_avg": 5.0}]
        self.respond({"approved": False})
        with patch("ai.bedrock.record_usage") as usage:
            self.assertFalse(self.supervisor.approve_result("Docker 과정 찾아줘", {"nickname": "private-user"},
                                                            plan, "공고 기준 미달입니다.", courses))
        payload, = self.requests()
        self.assertEqual(payload["courses"][0]["reason"], "컨테이너 학습")
        self.assertEqual(payload["reply_text"], "공고 기준 미달입니다.")
        self.assertNotIn("private", json.dumps(payload))
        self.assertNotIn("rating_avg", json.dumps(payload))
        self.assertIn("supervisor", usage.call_args.args[0])

    def test_invalid_result_verdicts_exhaust_one_retry_and_fail_closed(self):
        plan = self.supervisor.SupervisedTurn(chat.Turn(action="gap"))
        self.respond({"approved": 1}, {"approved": True, "reason": "invalid-extra-field"})
        with self.assertRaises(chat.ChatIntakeError) as caught:
            self.supervisor.approve_result("부족한 기술은?", {}, plan, "Docker 수준을 확인했습니다.", [])
        self.assertEqual(self.converse.call_count, 2)
        self.assertNotIn("invalid-extra-field", str(caught.exception))

    def test_supervised_turn_reaches_ten_converse_calls_with_all_retries(self):
        from backend import chat as counseling

        message = "Docker 교육과정을 추천해 주세요."
        reinterpret = {"decision": "reinterpret", "issue": "course_target", "quote": "Docker"}
        # 각 줄은 같은 단계의 두 응답이며 첫 응답에서 형식 검사가 실패한다.
        self.respond(
            {}, self.intake(scope="gap"),
            {}, reinterpret,
            {}, self.intake(targets=["Docker"]),
            {}, {"decision": "continue", "issue": "none"},
            {"approved": "true"}, {"approved": True},
        )
        course = {"gap_skill": "Docker", "title": "컨테이너 입문", "reason": "컨테이너 배포를 실습합니다.",
                  "match": "certain", "url": "https://example.com/docker"}
        worker_reply = counseling.Reply("Docker 과정입니다.", courses=[course], action="courses")
        with (patch("ai.chat.guard_input", return_value=True),
              patch("backend.supervised_courses.recommend_for_targets", return_value=worker_reply) as worker,
              patch("ai.bedrock.record_usage") as usage):
            reply = counseling.handle_message(counseling.Conversation(), message, job="cloud_engineer",
                                              sidebar_skills=[], architecture="supervised")

        worker.assert_called_once()
        self.assertEqual(worker.call_args.args[1], ["Docker"])
        self.assertEqual(reply.supervisor_decisions, ["reinterpret", "continue", "result_approved"])
        self.assertEqual(reply.courses, [course])
        self.assertFalse(reply.failed)
        stages = ["chat_supervised_intake", "chat_supervisor_plan",
                  "chat_supervised_intake", "chat_supervisor_plan", "chat_supervisor_result"]
        self.assertEqual([call.args[:2] for call in usage.call_args_list],
                         [(stage, attempt) for stage in stages for attempt in (1, 2)])
        self.assertEqual(self.converse.call_count, 10)

    def test_manager_cannot_add_writes_or_accept_invalid_decisions(self):
        bad = {"decision": "continue", "issue": "none", "save": True}
        self.respond(self.intake("note", scope="none"), bad, bad)
        with self.assertRaises(chat.ChatIntakeError):
            self.supervisor.interpret_turn("내 경험이야", {})

    def test_reinterpretation_cannot_invent_the_users_words(self):
        bad = {"decision": "reinterpret", "issue": "reference", "quote": "반드시 IAM을 사용해"}
        self.respond(self.intake(scope="gap"), bad, bad)
        with self.assertRaises(chat.ChatIntakeError):
            self.supervisor.interpret_turn("그 기술 과정 찾아줘", {})
        self.assertEqual(self.converse.call_count, 3)

    def test_approved_generic_gap_cannot_replace_an_unresolved_singular_reference(self):
        self.respond(self.intake(scope="gap"), {"decision": "continue", "issue": "none"})
        plan = self.supervisor.interpret_turn("그 기술을 다루는 강의 추천해줘", {"wishes": ["IAM", "Git"]})
        self.assertTrue(plan.question)
        self.assertEqual(plan.targets, [])

    def test_named_profile_write_without_only_cannot_save_other_facts(self):
        context = {"skills": ["Git", "Docker"], "experiences": ["제가 Git으로 배포했어요"], "job": "cloud_engineer"}
        message = "Docker를 프로필에 저장해줘"
        for excess in ({"save_skills": ["Docker", "Git"]}, {"save_job": True},
                       {"save_experiences": context["experiences"]}):
            values = {"save": True, "save_skills": ["Docker"], **excess}
            data = self.intake("note", scope="none", **values)
            with self.subTest(excess=excess), self.assertRaises(ValueError):
                self.supervisor._validate_candidate(data, message, context)

    def test_independently_requested_writes_can_coexist(self):
        context = {"skills": ["Docker", "Git"], "job": "cloud_engineer",
                   "experiences": ["제가 Git으로 배포했어요", "제가 Docker로 테스트했어요"]}
        message = "Docker를 저장해줘. 그리고 Git 경험을 저장해줘. 그리고 목표 직무도 저장해줘."
        data = self.intake("note", scope="none", save=True, save_skills=["Docker"],
                           save_experiences=context["experiences"][:1], save_job=True)
        self.supervisor._validate_candidate(data, message, context)
        data["turn"]["save_experiences"] = context["experiences"]
        with self.assertRaises(ValueError):
            self.supervisor._validate_candidate(data, message, context)

    def test_all_and_latest_correction_writes_remain_available(self):
        context = {"skills": ["Git", "Docker"], "last_changes": {"remove_skills": ["S3"]}}
        whole = self.intake("note", scope="none", save=True, save_skills=["Git", "Docker"])
        self.supervisor._validate_candidate(whole, "보유한 기술을 전부 저장해줘", context)
        latest = self.intake("note", scope="none", save=True, save_skills=["S3"])
        self.supervisor._validate_candidate(latest, "방금 정정한 내용도 저장해줘", context)


if __name__ == "__main__":
    unittest.main()
