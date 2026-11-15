"""ai/sections.py 칸 제목 판정 확인. 실행: python -m ai.test_sections"""
import logging

from ai.sections import EXCLUDED, OTHER, find_section, heading_value, weight


def demo():
    logging.disable(logging.INFO)  # 표에 없는 칸 제목 로그는 여기서 보지 않는다

    # 자격요건 아래 소제목 (2026-11-15 추가). 공백·/·괄호는 빼고 비교한다
    assert heading_value("프로그래밍/스크립팅") == "qualification"
    assert heading_value("기술 역량") == "qualification"
    assert heading_value("필수 경력(스킬)") == "qualification"

    # 제외 칸 (2026-11-15 추가)
    for name in ("복지 및 혜택", "다양한 혜택", "근무지", "연봉"):
        assert heading_value(name) == EXCLUDED, name

    # 일부러 넣지 않은 이름: 다른 뜻으로도 쓰여 표에 없다
    assert heading_value("경력") == OTHER

    # 기존 표
    assert heading_value("자격 요건") == "qualification"
    assert heading_value("주요업무") == "duty"
    assert heading_value("우대사항") == "preference"
    assert heading_value("회사 소개") == EXCLUDED
    assert heading_value("포지션소개") == OTHER

    # 본문에서 칸 찾기: 소제목 아래 기술은 자격요건, 제외 칸은 excluded, 칸 제목이 없으면 None
    text = "[자격요건]\n3년 이상\n[기술 역량]\nKubernetes 운영 경험\n[복지 및 혜택]\n장비 지원"
    assert find_section(text, "Kubernetes 운영 경험") == "qualification"
    assert find_section(text, "장비 지원") == EXCLUDED
    assert find_section("Python 경험 필수", "Python 경험 필수") is None

    # 가중치: 칸이 있으면 칸 기준, 칸이 없거나 모르는 칸이면 type 기준
    assert weight("preferred", "qualification") == 1.0
    assert weight("required", "other") == 1.0
    assert weight("preferred", None) == 0.5


if __name__ == "__main__":
    demo()
    print("통과")
