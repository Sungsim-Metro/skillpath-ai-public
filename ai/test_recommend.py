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
        # 두 과정 모두 uncertain 이라 LLM 이유 뒤에 코드의 고정 문장(#24)이 붙는다
        note = rec.UNCERTAIN_NOTE.format(skill="Terraform")
        assert [c["reason"] for c in recommendation["courses"]] == [f"{rec.MOCK_FALLBACK_REASON} {note}"] * 2
        assert result.attempts == 1 and result.usage["totalTokens"] == 0
    finally:
        rec.search_courses = original
        if mock_before is None:
            os.environ.pop("BEDROCK_MOCK", None)
        else:
            os.environ["BEDROCK_MOCK"] = mock_before


def test_uncertain_note_in_final_reason():
    """build_recommendation 이 돌려준 최종 reason 에 불확실 안내가 붙는지(도우미 함수가 아니라 실제 반환값, #24 리뷰).

    certain 과정에는 붙지 않고, uncertain 은 일반 안내, 지정 입문 과정은 입문 안내. review=False·True 두 경로 모두.
    과정 데이터 없이 돈다(후보와 입문 과정 목록을 바꿔 끼움, Bedrock 호출 없음).
    """
    def course(cid, match):
        return {"course_id": cid, "title": f"과정 {cid}", "level": "기초", "url": f"https://example.com/{cid}",
                "prerequisites": "표기 없음", "objectives": [f"Explain topic {cid}."], "subskill_id": "C1", "match": match}

    courses = [course("C-OK", "certain"), course("C-UN", "uncertain"), course("C-FD", "uncertain")]
    saved = rec.candidate_courses, rec._foundation_ids, os.environ.get("BEDROCK_MOCK")
    rec.candidate_courses = lambda skill, rules_only=False: [dict(c) for c in courses]
    rec._foundation_ids = lambda: {"ZZTest": ["C-FD"]}
    os.environ["BEDROCK_MOCK"] = "1"
    try:
        for review in (False, True):
            got, _ = rec.build_recommendation("ZZTest", {"postings": 4, "of": 12, "required_ratio": 0.5}, review=review)
            reasons = {c["url"].rsplit("/", 1)[1]: c["reason"] for c in got["courses"]}
            assert set(reasons) == {"C-OK", "C-UN", "C-FD"}, (review, reasons)
            general = rec.UNCERTAIN_NOTE.format(skill="ZZTest")
            foundation = rec.FOUNDATION_NOTE.format(skill="ZZTest")
            assert general not in reasons["C-OK"] and foundation not in reasons["C-OK"], (review, reasons["C-OK"])
            assert reasons["C-UN"].endswith(general) and foundation not in reasons["C-UN"], (review, reasons["C-UN"])
            assert reasons["C-FD"].endswith(foundation) and general not in reasons["C-FD"], (review, reasons["C-FD"])
            assert reasons["C-UN"] != general  # 안내만 남지 않고 원래 이유 뒤에 붙는다
            assert all(set(c) - {"rating_avg", "rating_count"} == {"title", "url", "level", "prerequisites", "reason", "match"} for c in got["courses"])
    finally:
        rec.candidate_courses, rec._foundation_ids = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("BEDROCK_MOCK", None)
        else:
            os.environ["BEDROCK_MOCK"] = saved[2]


def test_missing_foundation_file_falls_back_to_uncertain_note():
    # 공개 저장소에는 data/foundation_courses.json 이 없다. 그래도 uncertain 과정에는 일반 안내가 붙고 오류가 나지 않는다(11/21)
    saved = rec.FOUNDATION_FILE
    rec.FOUNDATION_FILE = rec.ROOT / "data" / "없는_foundation_courses.json"
    rec._foundation_ids.cache_clear()
    try:
        assert rec._foundation_ids() == {}
        note = rec.uncertain_note("ZZTest", {"match": "uncertain", "course_id": "C-FD"})
        assert note == rec.UNCERTAIN_NOTE.format(skill="ZZTest"), note
        assert rec.uncertain_note("ZZTest", {"match": "certain", "course_id": "C-FD"}) is None
    finally:
        rec.FOUNDATION_FILE = saved
        rec._foundation_ids.cache_clear()


if __name__ == "__main__":
    demo()
    test_uncertain_note_in_final_reason()
    test_missing_foundation_file_falls_back_to_uncertain_note()
    print("통과")
