"""모델 응답에서 JSON 객체를 읽는 규칙을 확인한다.

실행: 저장소 루트에서 `python -m ai.test_parse_json`
"""
import unittest

from ai.bedrock import parse_json_object

BODY = '{"corrections": [{"course_id": "A1", "reason": "닫는 } 가 들어간 글"}]}'
EXPECTED = {"corrections": [{"course_id": "A1", "reason": "닫는 } 가 들어간 글"}]}


class ParseJsonObjectTest(unittest.TestCase):
    def test_plain_and_fenced_objects(self):
        self.assertEqual(parse_json_object(BODY), EXPECTED)
        self.assertEqual(parse_json_object(f"```json\n{BODY}\n```"), EXPECTED)
        self.assertEqual(parse_json_object(f"```\n{BODY}\n```"), EXPECTED)

    def test_fence_with_text_around_it(self):
        """실제 모델 응답 두 모양: 울타리 뒤에 글이 붙은 것, 설명 글 뒤에 울타리가 오는 것."""
        self.assertEqual(parse_json_object(f"```json\n{BODY}\n```\n\n위와 같이 고쳤습니다."), EXPECTED)
        self.assertEqual(parse_json_object(f"검토자의 피드백을 분석합니다.\n\n**핵심 지적 사항:**\n1. 표현 수정\n\n```json\n{BODY}\n```"),
                         EXPECTED)

    def test_text_without_a_fence_is_still_rejected(self):
        for text in (f"고친 결과입니다. {BODY}", "", "검토자의 피드백을 분석합니다.", "```json\n[1, 2]\n```", "[1, 2]",
                     "```json\n{깨진 JSON}\n```"):
            with self.assertRaises(ValueError, msg=text):
                parse_json_object(text)


if __name__ == "__main__":
    unittest.main()
