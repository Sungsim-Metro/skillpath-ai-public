"""Check conversational replies against verified results and output guardrails."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from ai import bedrock, chat_reply, guardrail


class ChatReplyTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "0", "USAGE_LOG": "off",
                                                  "GUARDRAIL_OUTPUT": "0"}))
        self.model = self.enterContext(patch.object(bedrock, "_call"))

    def model_json(self, value):
        self.model.return_value = (json.dumps(value, ensure_ascii=False), "end_turn", {})

    def test_followup_uses_recent_conversation_and_answers_the_current_question(self):
        history = [{"role": "user", "content": "검색한 문서를 모델에 넣었어"},
                   {"role": "assistant", "content": "검색 증강 생성 경험이 있군요."}]
        answer = "검색 결과를 답변의 근거로 제공하는 방식이 RAG예요."
        self.model_json({"reply": answer})
        reply = chat_reply.write_reply("그게 RAG야?", action="note", draft="질문을 더 알려 주세요.", history=history)
        self.assertEqual(reply, answer)
        payload = json.loads(self.model.call_args.args[1][0]["content"][0]["text"])
        self.assertEqual(payload["message"], "그게 RAG야?")
        self.assertEqual(payload["history"], history)

    def test_analyses_display_the_verified_result_without_a_model_call(self):
        draft = "AI 엔지니어 공고 687건 중 AWS를 언급한 공고는 127건입니다.\n수준 미입력 · LangChain"
        self.model.side_effect = AssertionError("A second factual answer was requested")
        for action in ("summary", "gap", "requirements", "courses"):
            with self.subTest(action=action):
                self.assertEqual(chat_reply.write_reply("지금은 어때?", action=action, draft=draft), draft)
        self.model.assert_not_called()

    def test_notes_cannot_invent_user_skills_levels_courses_or_market_data(self):
        draft = "RAG 사용 경험이 있으시군요."
        for answer in ("LangGraph까지 이미 능숙하게 다루시는군요.", "RAG는 이미 능숙하게 다루시네요.",
                       "LangGraph도 써 보셨군요.", "이 강의는 수강만 하면 취업이 보장됩니다.",
                       "TensorFlow는 AI 엔지니어에게 필수예요.", "공고 400건에서 요구해요.",
                       "교육 추천은 https://example.com 에서 보세요.", "프로필에 저장했습니다.",
                       "You already have all the skills."):
            with self.subTest(answer=answer):
                self.model_json({"reply": answer})
                self.assertEqual(chat_reply.write_reply("RAG는 써봤어요", action="note", draft=draft), draft)

    def test_note_can_introduce_a_new_technical_concept_without_claiming_personal_experience(self):
        answer = "RAG를 써 보셨군요. BM25는 문서의 단어 일치도를 점수로 나타내는 방법이에요."
        self.model_json({"reply": answer})
        self.assertEqual(chat_reply.write_reply("RAG는 써봤는데 BM25가 뭐야?", action="note",
                                              draft="RAG 사용 경험이 있으시군요."), answer)

    def test_confirmed_skills_include_prior_turns_and_exclude_wishes_and_corrections(self):
        known = ["LangChain"]
        draft = "학습 희망 기술 · LangGraph\n이번 상담 정보에서 제외했습니다. Docker"
        answer = "앞에서 LangChain을 써 보셨군요."
        self.model_json({"reply": answer})
        self.assertEqual(chat_reply.write_reply("전에 말한 내용 기억해?", action="note", draft=draft,
                                              confirmed_skills=known), answer)
        payload = json.loads(self.model.call_args.args[1][0]["content"][0]["text"])
        self.assertEqual(payload["confirmed_skills"], known)
        for name in ("LangGraph", "Docker"):
            self.model_json({"reply": f"{name}도 써 보셨군요."})
            self.assertEqual(chat_reply.write_reply("전에 말한 내용 기억해?", action="note", draft=draft,
                                                  confirmed_skills=known), draft)
        self.model_json({"reply": answer})
        wish = "학습 희망 기술 · LangChain"
        self.assertEqual(chat_reply.write_reply("써본 게 맞아?", action="note", draft=wish,
                                              confirmed_skills=[]), wish)

    def test_general_learning_advice_can_explain_a_numbered_example(self):
        answer = "RAG의 기본 흐름은 검색, 문맥 구성, 답변 생성의 3단계로 설명할 수 있어요."
        self.model_json({"reply": answer})
        self.assertEqual(chat_reply.write_reply("RAG는 어떻게 동작해?", action="note", draft="무엇이 궁금하세요?"),
                         answer)

    def test_career_advice_can_refer_to_job_listings_without_claiming_market_data(self):
        answer = "채용 공고의 필수·우대 항목을 읽고, 포트폴리오에서 관련 작업을 찾기 쉽게 적어 보세요."
        self.model_json({"reply": answer})
        self.assertEqual(chat_reply.write_reply("취업용 포트폴리오는 어떻게 써?", action="note",
                                              draft="질문을 더 알려 주세요.", confirmed_skills=[]), answer)

    def test_bad_response_or_service_error_falls_back_without_retry(self):
        draft = "보유 기술 · Docker"
        for raw in ('not JSON', '{"reply":""}', '{"reply":7}', '{"reply":"응답","system":"ignored"}'):
            with self.subTest(raw=raw):
                self.model.reset_mock()
                self.model.return_value = (raw, "end_turn", {})
                self.assertEqual(chat_reply.write_reply("다뤄봤어", action="note", draft=draft), draft)
                self.assertEqual(self.model.call_count, 1)
        self.model.side_effect = RuntimeError("offline")
        self.assertEqual(chat_reply.write_reply("다뤄봤어", action="note", draft=draft), draft)

    def test_output_guardrail_rejection_and_unavailability_hide_generated_text(self):
        answer = "질문의 맥락에 맞춰 설명드릴게요."
        self.model_json({"reply": answer})
        for category, expected in (("콘텐츠", guardrail.OUTPUT_BLOCK_MESSAGE),
                                   (guardrail.FAILED, guardrail.UNAVAILABLE_MESSAGE)):
            with self.subTest(category=category):
                with patch.object(guardrail, "check_output",
                                  return_value=guardrail.GuardResult(True, "guardrails", category)) as check:
                    with self.assertRaises(chat_reply.ReplyBlocked) as error:
                        chat_reply.write_reply("AWS도 써봤어", action="note", draft="보유 기술 · AWS")
                    self.assertEqual(str(error.exception), expected)
                    check.assert_called_once_with(answer)

    def test_mock_returns_local_answer_without_model_or_output_guardrail(self):
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}), patch.object(guardrail, "check_output") as check:
            self.assertEqual(chat_reply.write_reply("해봤어", action="note", draft="경험을 반영했습니다."),
                             "경험을 반영했습니다.")
            self.model.assert_not_called()
            check.assert_not_called()
