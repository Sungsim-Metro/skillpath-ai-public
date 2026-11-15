"""ai/recommend.py 가 search_courses 결과를 받는 방식 확인. 실행: python -m ai.test_recommend

Bedrock 을 부르지 않는다. search_courses 를 잠깐 바꿔 끼워 받는 쪽만 본다.
"""
import os

import ai.recommend as rec


def demo():
    original = rec.search_courses
    mock_before = os.environ.get("BEDROCK_MOCK")
    try:
        # search_courses 가 돌려준 match 를 그대로 쓴다 (course_subskill.csv 의 값이 아니라)
        rec.search_courses = lambda skill, k=3: [
            {"course_id": "WU72QMK76B", "subskill_id": "C1-4", "match": "uncertain"},
            {"course_id": "JV8N39BFYH", "subskill_id": "C1-4", "match": "certain"},
        ]
        got = [(c["course_id"], c["match"]) for c in rec.candidate_courses("Docker")]
        assert got == [("WU72QMK76B", "uncertain"), ("JV8N39BFYH", "certain")], got

        # subskill_id 가 None 이면 search 가 certain 이라고 해도 uncertain (지정한 과정은 규칙으로 찾은 연결이 아니다)
        rec.search_courses = lambda skill, k=3: [
            {"course_id": "94T2BEN85A", "subskill_id": None, "match": "uncertain"},
            {"course_id": "RH22P2RXU4", "subskill_id": None, "match": "certain"},
        ]
        assert [(c["course_id"], c["match"]) for c in rec.candidate_courses("AWS")] == [
            ("94T2BEN85A", "uncertain"), ("RH22P2RXU4", "uncertain")]

        # 과정 데이터에 없는 course_id, 정해지지 않은 match 는 오류를 그대로 낸다
        rec.search_courses = lambda skill, k=3: [{"course_id": "NOPE", "subskill_id": "C1-4", "match": "certain"}]
        try:
            rec.candidate_courses("X")
            raise AssertionError("없는 과정인데 오류가 나지 않았습니다")
        except KeyError:
            pass
        rec.search_courses = lambda skill, k=3: [{"course_id": "WU72QMK76B", "subskill_id": "C1-4", "match": "maybe"}]
        try:
            rec.candidate_courses("X")
            raise AssertionError("match 가 이상한데 오류가 나지 않았습니다")
        except ValueError:
            pass

        # 과정이 없으면 빈 목록이고 LLM 을 부르지 않는다
        rec.search_courses = lambda skill, k=3: []
        recommendation, result = rec.build_recommendation("Go", {"postings": 4, "of": 40, "required_ratio": 0.25})
        assert recommendation["courses"] == [] and result is None

        # 가짜 모드에서 기술별 가짜 응답이 없는 기술도 오류 없이 추천이 나온다 (화면 연결·시연용)
        os.environ["BEDROCK_MOCK"] = "1"
        assert "Terraform" not in rec._mock_skills()
        rec.search_courses = lambda skill, k=3: [
            {"course_id": "RH22P2RXU4", "subskill_id": "C5-4", "match": "uncertain"},
            {"course_id": "KE11JMZA26", "subskill_id": "C5-4", "match": "uncertain"},
        ]
        recommendation, result = rec.build_recommendation("Terraform", {"postings": 14, "of": 40, "required_ratio": 0.36})
        assert [c["reason"] for c in recommendation["courses"]] == [rec.MOCK_FALLBACK_REASON] * 2
        assert result.attempts == 1 and result.usage["totalTokens"] == 0
    finally:
        rec.search_courses = original
        if mock_before is None:
            os.environ.pop("BEDROCK_MOCK", None)
        else:
            os.environ["BEDROCK_MOCK"] = mock_before


if __name__ == "__main__":
    demo()
    print("통과")
