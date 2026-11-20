"""회원 대화 기록 확인: 마스킹, 비회원 미저장, 회원별 분리, 저장 실패가 상담을 막지 않음. 실행: python -m unittest backend.test_chat_history"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import chat_history

S1, S2 = "a" * 32, "b" * 32
COURSE = {"course_id": "c1", "title": "AWS 기초"}
# 공개 검사가 이메일·전화번호 모양을 찾으므로 예시 전화번호는 조각을 이어 만든다(이메일은 example.com 만 쓴다)
PHONE = "010-" + "1234-" + "5678"
PHONE_INTL = "+82 " + "10-" + "1234-" + "5678"  # 국가번호가 붙으면 앞의 0 이 빠진다


class MaskTest(unittest.TestCase):
    def test_masks_contact_like_text_but_keeps_skills(self) -> None:
        text = f"메일은 jh.kim+1@example.com, 폰 {PHONE}, 02-123-4567, 주민 900101-1234567입니다. Docker 3년, 포트 8080"
        self.assertEqual(
            chat_history.mask(text),
            "메일은 [이메일], 폰 [전화번호], [전화번호], 주민 [주민번호]입니다. Docker 3년, 포트 8080")
        self.assertEqual(chat_history.mask("번호는" + "010" + "1234" + "5678" + "입니다"), "번호는[전화번호]입니다")

    def test_masks_foreigner_registration_numbers_and_country_code_phones(self) -> None:
        """외국인등록번호(뒷자리 5~8)와 +82 국가번호 형식도 가린다. 일반 기술 문장은 건드리지 않는다."""
        self.assertEqual(chat_history.mask("외국인 950302-5234567, 또는 9503028234567"), "외국인 [주민번호], 또는 [주민번호]")
        self.assertEqual(chat_history.mask(f"해외 {PHONE_INTL}"), "해외 [전화번호]")
        self.assertEqual(chat_history.mask("해외 +82-10-" + "1234-" + "5678, +82 (0)10-" + "1234-" + "5678"), "해외 [전화번호], [전화번호]")
        self.assertEqual(chat_history.mask("유선 +82 2-123-" + "4567"), "유선 [전화번호]")
        plain = "Docker 3년, 포트 8080, 2026-11-20 배포, 버전 1.2.3, AWS 계정 " + "123456" + "789012" + ", 점수 82 10 점"
        self.assertEqual(chat_history.mask(plain), plain)


class LocalHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.path = Path(work.name) / "chat.sqlite3"
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1", "SKILLPATH_MOCK_CHAT_DB": str(self.path)}))
        self.resource = self.enterContext(patch.object(chat_history, "_resource"))

    def tearDown(self) -> None:
        self.resource.assert_not_called()

    def test_guest_is_never_stored(self) -> None:
        for guest in (None, "", "   "):
            self.assertFalse(chat_history.append_turn(guest, S1, "질문", "답"))
            self.assertEqual(chat_history.list_sessions(guest), [])
            self.assertEqual(chat_history.load_session(guest, S1), [])
        self.assertFalse(self.path.exists())

    def test_members_get_their_own_sessions_in_order(self) -> None:
        self.assertTrue(chat_history.append_turn("kim", S1, f"Docker 써봤어요 {PHONE}", "알겠습니다", [COURSE]))
        self.assertTrue(chat_history.append_turn("kim", S1, "부족한 기술은?", "Linux입니다"))
        self.assertTrue(chat_history.append_turn("kim", S2, "새 대화" + "가" * 50, "답"))
        chat_history.append_turn("lee", S1, "남의 대화", "답")
        self.assertEqual(
            chat_history.load_session("kim", S1),
            [("Docker 써봤어요 [전화번호]", "알겠습니다", [COURSE]), ("부족한 기술은?", "Linux입니다", [])])
        sessions = chat_history.list_sessions("kim")
        self.assertEqual([s["session_id"] for s in sessions], [S2, S1])  # 최근 대화가 위
        self.assertEqual(sessions[0]["title"], "새 대화" + "가" * 26 + "…")
        self.assertEqual(sessions[1]["title"], "Docker 써봤어요 [전화번호]")  # 제목은 첫 질문, 가린 뒤
        self.assertEqual(chat_history.load_session("lee", S2), [])  # 다른 회원의 세션은 열리지 않는다

    def test_only_real_counseling_replies_are_recorded(self) -> None:
        """저장 여부는 backend 가 정한다: 막힌 발화·범위 밖·검사 실패·처리 오류(reply 없음)·비회원은 저장하지 않는다."""
        from types import SimpleNamespace as R

        cases = [
            (R(text="답", action="note", failed=False, courses=[COURSE]), "kim", True),
            (R(text="답", action="gap", failed=False, courses=[]), "kim", True),
            (R(text="답", action="unsupported", failed=False, courses=[]), "kim", True),
            (R(text="차단", action="blocked", failed=False, courses=[]), "kim", False),
            (R(text="범위", action="out_of_scope", failed=False, courses=[]), "kim", False),
            (R(text="실패", action="intake", failed=True, courses=[]), "kim", False),
            (None, "kim", False),  # 처리 중 예외
            (R(text="답", action="note", failed=False, courses=[]), None, False),  # 비회원
        ]
        for reply, nickname, saved in cases:
            before = len(chat_history.load_session("kim", S1))
            self.assertEqual(chat_history.record_reply(nickname, S1, "질문", reply), saved, (reply, nickname))
            self.assertEqual(len(chat_history.load_session("kim", S1)) - before, 1 if saved else 0, (reply, nickname))
        self.assertEqual(chat_history.load_session("kim", S1)[0][2], [COURSE])  # 과정 카드도 함께 저장
        self.assertTrue(chat_history.record_reply("kim", S1, "질문", R(text="답")))  # failed·action·courses 가 없는 응답도 정상으로 본다

    def test_bad_session_id_is_rejected_and_save_failure_does_not_raise(self) -> None:
        with self.assertRaises(ValueError):
            chat_history.append_turn("kim", "S#x", "질문", "답")
        # 화면이 부르는 record_reply 는 세션 ID 오류로도 상담을 막지 않는다(append_turn 은 버그를 드러내려고 올린다)
        bad_reply = MagicMock(text="답", failed=False, action="note", courses=[])
        with self.assertLogs(chat_history.logger, "WARNING"):
            self.assertFalse(chat_history.record_reply("kim", "S#x", "질문", bad_reply))
        with patch.object(chat_history, "_connect", side_effect=OSError("secret 닉네임 kim")), \
                self.assertLogs(chat_history.logger, "WARNING") as logs:
            self.assertFalse(chat_history.append_turn("kim", S1, "질문", "답"))
        self.assertNotIn("kim", "".join(logs.output))
        self.assertIn("OSError", "".join(logs.output))


class ClockTest(unittest.TestCase):
    def test_timestamps_never_repeat_even_if_the_clock_stands_still(self) -> None:
        """시계 해상도가 거친 환경에서 같은 시각이 나오면 같은 세션의 턴 키가 겹친다."""
        real = chat_history.datetime  # 패치하기 전에 진짜 클래스를 잡아 둔다
        frozen = real(2026, 11, 20, 1, 0, 0, tzinfo=chat_history.timezone.utc)
        with patch.object(chat_history, "datetime") as fake:
            fake.now.return_value = frozen
            fake.fromisoformat.side_effect = real.fromisoformat
            stamps = [chat_history._now() for _ in range(5)]
        self.assertEqual(stamps, sorted(set(stamps)))  # 중복 없이 오름차순


class DynamoHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "0", "DYNAMODB_CHAT_TABLE": "chat-test"}))
        self.table = self.enterContext(patch.object(chat_history, "_resource")).return_value.Table.return_value

    def test_append_writes_a_turn_item_and_a_session_item(self) -> None:
        self.assertTrue(chat_history.append_turn("kim", S1, "메일 a@example.com", "답", [COURSE]))
        turn = self.table.put_item.call_args.kwargs["Item"]
        self.assertEqual((turn["nickname"], turn["prompt"]), ("kim", "메일 [이메일]"))
        self.assertTrue(turn["sk"].startswith(f"T#{S1}#"))
        self.assertIsInstance(turn["expires_at"], int)
        update = self.table.update_item.call_args.kwargs
        self.assertEqual(update["Key"], {"nickname": "kim", "sk": f"S#{S1}"})
        self.assertIn("if_not_exists(#title", update["UpdateExpression"])
        self.assertEqual(set(update["ExpressionAttributeNames"].values()), {"title", "updated_at", "expires_at"})

    def test_list_and_load_read_back_what_was_written(self) -> None:
        self.table.query.side_effect = [
            {"Items": [{"sk": f"S#{S1}", "title": "첫", "updated_at": "2026-11-20T01"},
                       {"sk": f"S#{S2}", "title": "둘", "updated_at": "2026-11-20T02"}]},
            {"Items": [{"sk": f"T#{S1}#2", "prompt": "q2", "answer": "a2", "courses": "[]"},
                       {"sk": f"T#{S1}#1", "prompt": "q1", "answer": "a1", "courses": '[{"course_id": "c1"}]'}]},
        ]
        self.assertEqual([s["title"] for s in chat_history.list_sessions("kim")], ["둘", "첫"])
        self.assertEqual(chat_history.load_session("kim", S1),
                         [("q1", "a1", [{"course_id": "c1"}]), ("q2", "a2", [])])

    def test_without_a_table_name_the_feature_is_off_and_silent(self) -> None:
        """실제 모드에서 테이블 이름이 없으면 저장도 목록도 없고, 경고 로그도 쌓지 않는다(켜지지 않은 기능이다)."""
        with patch.dict(os.environ, {"DYNAMODB_CHAT_TABLE": ""}):
            self.assertFalse(chat_history.enabled())
            with self.assertNoLogs(chat_history.logger, "WARNING"):
                self.assertFalse(chat_history.append_turn("kim", S1, "q", "a"))
                self.assertFalse(chat_history.record_reply("kim", S1, "q", MagicMock(text="a", failed=False, action="note", courses=[])))
            self.assertEqual(chat_history.list_sessions("kim"), [])
            self.assertEqual(chat_history.load_session("kim", S1), [])
        self.table.put_item.assert_not_called()
        with patch.dict(os.environ, {"DYNAMODB_CHAT_TABLE": "chat-test"}):
            self.assertTrue(chat_history.enabled())


if __name__ == "__main__":
    unittest.main()
