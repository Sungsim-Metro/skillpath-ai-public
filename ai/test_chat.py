"""학습 상담 입력과 Bedrock 호출 경계를 AWS 없이 확인한다."""
import json
import os
import unittest
from dataclasses import asdict
from unittest.mock import patch

from ai import chat


class MockIntakeTest(unittest.TestCase):
    def setUp(self):
        self.mock_mode = patch.dict(os.environ, BEDROCK_MOCK="1")
        self.mock_mode.start()
        self.addCleanup(self.mock_mode.stop)

    def test_facts_belong_to_speaker(self):
        turn = chat.interpret("팀은 Kubernetes를 썼고 저는 Docker로 API를 배포했어요. "
                              "Terraform은 배우고 싶어요.", {})
        self.assertEqual(turn.add_skills, ["Docker"])
        self.assertEqual(len(turn.add_experiences), 1)
        self.assertIn("API", turn.add_experiences[0])
        self.assertNotIn("Kubernetes", turn.add_experiences[0])
        self.assertEqual(turn.action, "note")
        self.assertFalse(turn.save)

    def test_known_skill_is_repeated_only_for_a_new_personal_use_statement(self):
        context = {"skills": ["Docker"], "experiences": ["전에 제가 Docker로 API를 배포했어요"]}
        repeated = chat.interpret("이번에도 제가 도커를 사용했어요.", context)
        self.assertEqual(repeated.add_skills, ["Docker"])
        self.assertFalse(repeated.save)
        for message in ["Docker만 프로필에 저장해줘", "내 Docker 경험을 정리해줘",
                        "Docker를 더 배우고 싶어요", "팀원이 Docker를 사용했어요"]:
            with self.subTest(message=message):
                self.assertEqual(chat.interpret(message, context).add_skills, [])

    def test_negative_experience_is_removed_from_confirmed_state(self):
        experience = "제가 Docker로 API를 배포했어요"
        turn = chat.interpret("정정할게요. Docker는 제가 쓴 게 아니에요.", {
            "skills": ["Docker"], "experiences": [experience]})
        self.assertEqual(turn.remove_skills, ["Docker"])
        self.assertEqual(turn.remove_experiences, [experience])
        self.assertFalse(turn.save)
        self.assertFalse(turn.add_experiences)
        turn = chat.interpret("Docker 경험은 저장하지 않았어요.", {"skills": ["Docker"]})
        self.assertFalse(turn.remove_skills)
        self.assertFalse(turn.save)

    def test_saved_experience_supplies_read_only_skills(self):
        direct = "제가 Docker로 API를 배포했어요"
        context = {"experiences": [direct, "Terraform 강의를 추천하는 앱을 개발했어요",
                                   "팀원이 Kubernetes를 사용했고 저는 API를 설계했어요",
                                   "Linux를 배우고 싶어요", "강의에서 Git을 배웠어요"]}
        for message in ["뭐가 부족해?", "내 상태만 정리해줘", "어떤 강의를 들을까?"]:
            with self.subTest(message=message):
                turn = chat.interpret(message, context)
                self.assertEqual(turn.experience_skills, ["Docker"])
                self.assertFalse(turn.add_skills)
                self.assertFalse(turn.save)
        only_course = {"experiences": ["Docker 강의를 추천하는 앱을 개발했어요"]}
        self.assertEqual(chat.interpret("뭐가 부족해?", only_course).experience_skills, [])
        correction = chat.interpret("Docker는 제가 쓴 게 아니에요.", context)
        self.assertEqual(correction.remove_experiences, [direct])
        self.assertEqual(correction.remove_skills, ["Docker"])
        self.assertEqual(correction.experience_skills, [])
        turn = chat.interpret("Docker만 저장해줘", context)
        self.assertEqual(turn.save_skills, ["Docker"])
        self.assertFalse(turn.save_experiences)
        self.assertFalse(turn.add_skills)
        corrected = dict(context, excluded_skills=["Docker"])
        self.assertEqual(chat.interpret("내 상태 정리해줘", corrected).experience_skills, [])

    def test_unlisted_skills_require_direct_use_or_a_marked_label(self):
        for text, expected in [
            ("제가 OpenTofu를 사용했어요", ["OpenTofu"]),
            ("제가 OpenTofu와 AtlasFlux를 사용했어요", ["OpenTofu", "AtlasFlux"]),
            ("제가 GitHub Actions를 사용했어요", ["GitHub Actions"]),
            ("제가 Atlas.Flow를 사용했어요", ["Atlas.Flow"]),
            ("저는 `데이터 품질 검사`를 사용했어요", ["데이터 품질 검사"]),
            ("저는 ‘데이터 품질 검사’를 사용했어요", ["데이터 품질 검사"]),
            ("제가 `도커`를 사용했어요", ["Docker"]),
            ("팀원이 OpenTofu를 사용했고 저는 API를 배포했어요", []),
            ("OpenTofu를 써 보고 싶어요", []),
            ("OpenTofu를 사용하지 않았어요", []),
            ("저는 문서를 사용했어요", []),
            ("OpenTofu 프로젝트를 개발했어요", []),
            ("OpenTofu 강의를 추천하는 앱을 만들었어요", []),
            ("OpenTofu를 사용하지 않았지만 저는 AtlasFlux를 사용했어요", ["AtlasFlux"]),
        ]:
            with self.subTest(text=text):
                turn = chat.interpret(text, {})
                self.assertEqual(turn.add_skills, expected)
                self.assertFalse(turn.save)
        explicit = chat.interpret("제가 OpenTofu를 사용했어요. OpenTofu만 프로필에 저장해줘", {})
        self.assertEqual(explicit.save_skills, ["OpenTofu"])
        self.assertFalse(explicit.save_experiences)

    def test_named_unlisted_experiences_and_corrections_keep_their_scope(self):
        experience = "제가 OpenTofu로 인프라를 구축했어요"
        other = "제가 Docker로 앱을 배포했어요"
        context = {"experiences": [experience, other], "last_changes": {"add_experiences": [other]}}
        self.assertEqual(chat.interpret("내 상태를 정리해줘", context).experience_skills, ["OpenTofu", "Docker"])
        selected = chat.interpret("OpenTofu 경험만 프로필에 저장해줘", context)
        self.assertEqual(selected.save_experiences, [experience])
        self.assertFalse(selected.save_skills)
        self.assertEqual(chat.interpret("opentofu 기술만 저장해줘", context).save_skills, ["OpenTofu"])
        unknown = chat.interpret("방금 말한 AtlasFlux 경험만 프로필에 저장해줘", context)
        self.assertFalse(unknown.save_experiences)
        self.assertFalse(unknown.save_skills)
        corrected = chat.interpret("OpenTofu는 제가 쓴 게 아니에요", context)
        self.assertEqual(corrected.remove_skills, ["OpenTofu"])
        self.assertEqual(corrected.remove_experiences, [experience])
        removed = {"excluded_skills": ["OpenTofu"], "last_changes": {"remove_skills": ["OpenTofu"]}}
        self.assertEqual(chat.interpret("정정한 OpenTofu만 저장해줘", removed).save_skills, ["OpenTofu"])

    def test_tool_use_with_a_multiword_object_needs_a_concrete_action(self):
        experience = "제가 Podman으로 컨테이너 이미지를 배포했어요"
        for text in [experience, "제가 Podman으로 서비스 컨테이너 이미지를 직접 배포했어요",
                     "제가 Podman을 이용해 컨테이너 이미지를 배포했어요"]:
            with self.subTest(text=text):
                turn = chat.interpret(text + ".", {})
                self.assertEqual(turn.add_skills, ["Podman"])
                self.assertEqual(turn.add_experiences, [text])
                self.assertFalse(turn.save)
        self.assertEqual(chat.interpret("내 경험 요약해줘", {"experiences": [experience]}).experience_skills, ["Podman"])
        for text in ["Podman으로 유명한 프로젝트를 분석했어요.",
                     "Podman으로 컨테이너 이미지를 배포하고 싶어요.", "팀원이 Podman으로 컨테이너 이미지를 배포했어요."]:
            with self.subTest(text=text):
                self.assertFalse(chat.interpret(text, {}).add_skills)

    def test_trying_a_named_tool_records_only_its_name(self):
        for text in ["Podman을 이용해 이것저것 해봤다", "제가 Podman으로 이것저것 해봤어요", "제가 Podman을 이용했어요"]:
            with self.subTest(text=text):
                self.assertEqual(chat.interpret(text, {}), chat.Turn(add_skills=["Podman"]))
        for text in ["Podman으로 이것저것 해보지 않았어요", "Podman을 이용해 이것저것 해보고 싶어요",
                     "팀원이 Podman으로 이것저것 해봤어요", "이것저것 해봤어요"]:
            with self.subTest(text=text):
                self.assertEqual(chat.interpret(text, {}), chat.Turn())

    def test_incomplete_across_turns(self):
        original = "그리고 배포할 때는 제가…"
        self.assertEqual(chat.interpret(original, {}).status, "incomplete")
        self.assertFalse(chat.interpret("Docker를…", {}).add_skills)
        self.assertEqual(chat.interpret("Docker로 배포했어요", {
            "history": [{"role": "user", "content": original}]}).add_skills, ["Docker"])
        self.assertEqual(chat.interpret("사용했어요", {
            "history": [{"role": "user", "content": "제가 OpenTofu를…"}]}).add_skills, ["OpenTofu"])

    def test_requests_and_deferrals(self):
        for text, action in [
            ("내 상태 좀 봐줘", "summary"), ("부족한 게 뭐야?", "gap"),
            ("클라우드 엔지니어 기준으로 무엇부터 공부할까?", "gap"),
            ("그중 먼저 들을 교육과정을 추천해줘", "courses"),
            ("아직 추천은 하지 마. 부족한 기술만 알려줘", "gap"),
            ("교육과정 추천은 나중에 해줘", "note"),
            ("Docker 교육과정을 수강했어요", "note"),
            ("Docker 강의를 추천하는 앱을 개발했어요", "note"),
            ("네트워크 경험이 부족해요", "note"),
            ("강의를 듣고 싶어요", "note"),
            ("강의를 들었는데 지금 뭐가 부족해?", "gap"),
            ("강의는 듣지 않을 거야. 내 상태만 알려줘.", "summary"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(chat.interpret(text, {}).action, action)

    def test_goal_is_distinct_from_experience(self):
        for text, status in [
            ("AI 엔지니어로 취업하려면 뭘 준비할까?", "ok"),  # 2026-11-19 AI 엔지니어 지원
            ("AI 인프라를 준비 중인데 무엇부터 공부할까?", "clarify_role"),
            ("클라우드 직무를 준비하며 AI 모델을 배포했어요.", "ok"),
            ("AI 엔지니어와 협업해 Python으로 모델을 배포했어요.", "ok"),
            ("원래는 AI 엔지니어를 목표로 했지만 지금은 클라우드 엔지니어를 준비 중입니다.", "ok"),
            ("AI 엔지니어로 일한 뒤 클라우드 엔지니어를 준비해요", "ok"),
            ("팀원이 AI 엔지니어 취업을 준비했고, 나는 API를 배포했다.", "ok"),
            ("백엔드 개발자로 이직할 건데 뭘 공부할까?", "unsupported"),
            ("프론트엔드로 취업하려고 해. 어떤 과정이 좋아?", "unsupported"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(chat.interpret(text, {"job": "cloud_engineer"}).status, status)

    def test_ai_engineer_goal_is_supported(self):
        turn = chat.interpret("AI 엔지니어로 취업하려면 뭘 준비할까?", {"job": "cloud_engineer"})
        self.assertEqual((turn.status, turn.job), ("ok", "ai_engineer"))
        self.assertEqual(chat.interpret("AI 직무 기준으로 부족한 역량 알려줘", {}).job, "ai_engineer")

    def test_role_confirmation_picks_the_named_job(self):
        history = [{"role": "user", "content": "AI 인프라가 목표인데 어떤 과정을 들을까?"},
                   {"role": "assistant", "content": "클라우드 엔지니어 또는 AI 엔지니어 중 어느 직무를 기준으로 상담할까요?"}]
        self.assertEqual(chat.interpret("AI 엔지니어 기준으로", {"history": history}).job, "ai_engineer")
        self.assertEqual(chat.interpret("클라우드로 할게요", {"history": history}).job, "cloud_engineer")
        self.assertIsNone(chat.interpret("네", {"history": history, "needs_role": True}).job)  # 둘 중 고르지 않았다

    def test_role_confirmation_resumes_prior_question(self):
        history = [{"role": "user", "content": "AI 인프라가 목표인데 어떤 과정을 들을까?"},
                   {"role": "assistant", "content": "클라우드 엔지니어 기준으로 상담할까요?"}]
        turn = chat.interpret("네, 클라우드 기준으로", {"history": history})
        self.assertEqual((turn.job, turn.action), ("cloud_engineer", "courses"))

    def test_targeted_save_and_mixed_request(self):
        docker = "Docker로 API를 배포했어요"
        context = {"skills": ["Docker", "Python"], "experiences": [docker, "Python으로 수집했어요"],
                   "last_changes": {"add_experiences": [docker], "add_skills": ["Docker"]}}
        for message in ["방금 말한 Docker 배포 경험만 프로필에 저장해줘", "이 경험을 기억해줘"]:
            turn = chat.interpret(message, context)
            self.assertTrue(turn.save)
            self.assertEqual(turn.save_skills, [])
            self.assertEqual(turn.save_experiences, [docker])
        turn = chat.interpret("Docker만 저장하고 뭐가 부족한지 봐줘", context)
        self.assertTrue(turn.save)
        self.assertEqual(turn.save_skills, ["Docker"])
        self.assertEqual(turn.save_experiences, [])
        self.assertEqual(turn.action, "gap")
        self.assertFalse(chat.interpret("프로필 저장은 하지 말아줘", context).save)
        self.assertEqual(chat.interpret("저장해줘", {}).save_skills, [])

    def test_save_prior_correction(self):
        context = {"skills": ["Git"], "last_changes": {"remove_skills": ["Docker"]}}
        turn = chat.interpret("방금 정정한 Docker만 프로필에 반영해줘", context)
        self.assertTrue(turn.save)
        self.assertEqual(turn.save_skills, ["Docker"])
        self.assertFalse(turn.add_skills)

    def test_save_obeys_restrictions_and_cancellations(self):
        context = {"skills": ["Docker", "Python"]}
        for message in ["Docker는 저장하지 말고 Python만 저장해줘.",
                        "Docker 말고 Python만 프로필에 저장해줘."]:
            with self.subTest(message=message):
                self.assertEqual(chat.interpret(message, context).save_skills, ["Python"])
        for message in ["Docker를 저장해줘. 아니, 아직은 저장하지 마.",
                        "Docker만 저장해줘야 하나요?", "Docker를 저장해 두지 마",
                        "S3에 파일을 저장하고 Python으로 데이터를 분석했어요"]:
            with self.subTest(message=message):
                self.assertFalse(chat.interpret(message, context).save)

    def test_an_explicit_pending_save_accepts_only_its_target(self):
        context = {"skills": ["Docker", "Python"], "pending_save": True,
                   "experiences": ["Docker로 API를 배포했어요"]}
        turn = chat.interpret("Docker만", context)
        self.assertTrue(turn.save)
        self.assertEqual(turn.save_skills, ["Docker"])
        turn = chat.interpret("방금 말한 Docker 배포 경험", context)
        self.assertTrue(turn.save)
        self.assertEqual(turn.save_experiences, context["experiences"])
        for message in ["아니요", "취소할게요", "Docker를 사용했어요", "Docker 강의 추천해줘"]:
            with self.subTest(message=message):
                self.assertFalse(chat.interpret(message, context).save)
        self.assertFalse(chat.interpret("Docker만", {}).save)


class RealBoundaryTest(unittest.TestCase):
    @patch("ai.bedrock._client")
    def test_model_receives_the_restatement_rule_and_retains_distinct_turns(self, client):
        context = {"skills": ["Docker"], "experiences": ["제가 Docker로 API를 배포했어요"]}
        converse = client.return_value.converse
        for text, expected in [
            ("이번에도 제가 도커를 사용했어요", chat.Turn(add_skills=["Docker"])),
            ("Docker만 저장해줘", chat.Turn(save=True, save_skills=["Docker"])),
            ("내 Docker 경험을 정리해줘", chat.Turn(action="summary", experience_skills=["Docker"])),
        ]:
            with self.subTest(text=text), patch.dict(os.environ, BEDROCK_MODEL_ID="test"):
                converse.return_value = {"output": {"message": {"content": [{"text": json.dumps(asdict(expected))}]}}}
                self.assertEqual(chat.interpret(text, context, screened=True), expected)
        request = converse.call_args.kwargs
        self.assertIn("이미 있어도 add_skills에 포함", request["system"][0]["text"])
        self.assertEqual(json.loads(request["messages"][0]["content"][0]["text"])["context"], context)

    def test_tool_trial_has_skill_evidence_and_no_evidence_of_specific_work(self):
        result = asdict(chat.Turn(add_skills=["Podman"]))
        for text in ["Podman을 이용해 이것저것 해봤다", "제가 Podman으로 이것저것 해봤어요"]:
            with self.subTest(text=text):
                chat._validate(result, text, {})
                with self.assertRaises(ValueError):
                    chat._validate(dict(result, add_experiences=["Podman으로 이미지를 배포했어요"]), text, {})
        for text in ["Podman으로 이것저것 해보지 않았어요", "Podman을 이용해 이것저것 해보고 싶어요",
                     "팀원이 Podman으로 이것저것 해봤어요"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                chat._validate(result, text, {})

    def test_unlisted_skill_must_be_grounded_in_the_speakers_usage(self):
        label = "Acme.Flow"
        addition = asdict(chat.Turn(add_skills=[label]))
        chat._validate(addition, "제가 Acme.Flow를 사용했습니다.", {})
        for message in ["팀원이 Acme.Flow를 사용했습니다.", "Acme.Flow를 배우고 싶어요.",
                        "제가 Acme.Flow를 사용한 적은 없어요.", "제가 Acme.FlowPlus를 사용했어요."]:
            with self.subTest(message=message), self.assertRaises(ValueError):
                chat._validate(addition, message, {})

    def test_unlisted_target_does_not_select_a_different_saved_experience(self):
        right = "제가 Acme.Flow로 배포했어요"
        wrong = "제가 Docker로 배포했어요"
        context = {"experiences": [right, wrong]}
        message = "Acme.Flow 경험만 프로필에 저장해줘"
        for target in [message, message.replace("경험만", "경험을")]:
            chat._validate(asdict(chat.Turn(save=True, save_experiences=[right])), target, context)
            with self.assertRaises(ValueError):
                chat._validate(asdict(chat.Turn(save=True, save_experiences=[wrong])), target, context)
        chat._validate(asdict(chat.Turn(save=True, save_skills=["ACME.FLOW"])),
                       "acme.flow만 저장해줘", {"skills": ["Acme.Flow"]})
        with self.assertRaises(ValueError):
            chat._validate(asdict(chat.Turn(save=True, save_experiences=[wrong])),
                           "PolarisFlow와 Docker 경험만 저장해줘", context)

    def setUp(self):
        self.config = patch.dict(os.environ, USAGE_LOG="off", BEDROCK_MOCK="0", BEDROCK_GUARDRAIL_ID="test",
                                 BEDROCK_GUARDRAIL_VERSION="1")
        self.config.start()
        self.addCleanup(self.config.stop)

    @patch("ai.bedrock._client")
    def test_missing_config_stops_before_aws(self, client):
        with patch.dict(os.environ, BEDROCK_GUARDRAIL_VERSION=""):
            with self.assertRaises(chat.ChatIntakeError):
                chat.interpret("원문", {})
        client.assert_not_called()

    @patch("ai.bedrock._client")
    def test_intervention_returns_inert_turn_and_exact_raw_input(self, client):
        # 실제 ApplyGuardrail 의 INTERVENED 응답은 무엇이 개입했는지 assessments 에 담는다(차단 주제 BLOCKED 예)
        client.return_value.apply_guardrail.return_value = {"action": "GUARDRAIL_INTERVENED", "assessments": [
            {"topicPolicy": {"topics": [{"name": "Personal attribute judgment", "type": "DENY", "action": "BLOCKED"}]}}]}
        raw = "  원문 메시지  "
        self.assertEqual(chat.interpret(raw, {"skills": ["Git"]}), chat.Turn(status="blocked"))
        client.return_value.apply_guardrail.assert_called_once_with(
            guardrailIdentifier="test", guardrailVersion="1", source="INPUT",
            content=[{"text": {"text": raw}}])
        client.return_value.converse.assert_not_called()
        # 개입했다는데 판정을 읽을 수 없는 응답은 차단이 아니라 검사 실패다(통과시키지도 않는다, #24 공통 가드레일)
        client.return_value.apply_guardrail.return_value = {"action": "GUARDRAIL_INTERVENED"}
        with self.assertRaises(chat.ChatIntakeError):
            chat.interpret(raw, {"skills": ["Git"]})
        client.return_value.converse.assert_not_called()

    @patch("ai.bedrock._client")
    def test_unknown_guard_response_or_failure_stops_before_converse(self, client):
        for response in [{}, {"action": "UNRECOGNIZED"}]:
            client.return_value.apply_guardrail.return_value = response
            with self.assertRaises(chat.ChatIntakeError):
                chat.interpret("원문", {})
        client.return_value.apply_guardrail.side_effect = RuntimeError("secret-input")
        with self.assertRaises(chat.ChatIntakeError) as caught:
            chat.interpret("secret-input", {})
        self.assertNotIn("secret-input", str(caught.exception))
        client.return_value.converse.assert_not_called()

    @patch("ai.bedrock._client")
    def test_screened_input_uses_json_retry_and_safe_validation(self, client):
        valid = asdict(chat.Turn(action="summary"))

        def reply(payload):
            return {"output": {"message": {"content": [{"text": json.dumps(payload)}]}}}

        client.return_value.converse.side_effect = [reply(dict(valid, save="yes")), reply(valid)]
        with patch.dict(os.environ, BEDROCK_MODEL_ID="test"):
            turn = chat.interpret("상태 정리", {"job": "cloud_engineer"}, screened=True)
        self.assertEqual(turn.action, "summary")
        self.assertEqual(client.return_value.converse.call_count, 2)
        client.return_value.apply_guardrail.assert_not_called()

    @patch("ai.bedrock._client")
    def test_invalid_model_output_never_exposes_the_response(self, client):
        invalid = dict(asdict(chat.Turn()), job="unsupported")
        client.return_value.converse.return_value = {
            "output": {"message": {"content": [{"text": json.dumps(invalid)}]}}}
        with patch.dict(os.environ, BEDROCK_MODEL_ID="test"):
            with self.assertRaises(chat.ChatIntakeError) as caught:
                chat.interpret("secret-input", {}, screened=True)
        self.assertNotIn("unsupported", str(caught.exception))
        self.assertNotIn("secret-input", str(caught.exception))
        self.assertEqual(client.return_value.converse.call_count, 2)

    def test_write_authorization_is_checked_against_user_text(self):
        context = {"skills": ["Docker", "Git"]}
        wrong = asdict(chat.Turn(save=True, save_skills=["Docker", "Git"]))
        for message in ["내 상태 좀 정리해줘", "Docker만 저장해줘",
                        "Docker 말고 Git만 저장해줘", "저장은 아직 하지 마"]:
            with self.subTest(message=message), self.assertRaises(ValueError):
                chat._validate(wrong, message, context)
        scoped = asdict(chat.Turn(save=True, save_skills=["Docker"]))
        chat._validate(scoped, "Docker만 저장해줘", context)
        chat._validate(scoped, "Docker만", dict(context, pending_save=True))
        with self.assertRaises(ValueError):
            chat._validate(scoped, "Docker만", context)

    def test_projected_skills_require_current_owned_experience_evidence(self):
        message = "Docker만 저장해줘"
        payload = asdict(chat.Turn(save=True, save_skills=["Docker"], experience_skills=["Docker"]))
        direct = "제가 도커로 API를 배포했어요"
        chat._validate(payload, message, {"experiences": [direct]})
        with self.assertRaises(ValueError):
            chat._validate(dict(payload, save_experiences=[direct]), "Docker 배포 경험을 저장해줘", {"experiences": [direct]})
        correction = asdict(chat.Turn(remove_skills=["Docker"], remove_experiences=[direct]))
        chat._validate(correction, "제가 Docker를 쓴 건 아니었어요", {"experiences": [direct]})
        for text in ["Docker 강의를 추천하는 앱을 개발했어요", "팀원이 Docker로 배포했어요",
                     "Docker를 써 보고 싶어요", "Python으로 API를 배포했어요"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                chat._validate(payload, message, {"experiences": [text]})
        for deleted in [{"remove_skills": ["Docker"]}, {"remove_experiences": [direct]}]:
            with self.subTest(deleted=deleted), self.assertRaises(ValueError):
                chat._validate(dict(payload, **deleted), message, {"experiences": [direct], "skills": ["Docker"]})
        with self.assertRaises(ValueError):
            chat._validate(payload, message, {"experiences": [direct], "excluded_skills": ["Docker"]})


if __name__ == "__main__":
    unittest.main()
