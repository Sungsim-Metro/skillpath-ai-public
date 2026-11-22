"""추출 v2.1 후처리(ai/extract.py) 확인. 실행: python -m ai.test_extract_v2

Bedrock 을 부르지 않는다. LLM 이 낸 것처럼 만든 출력을 to_contract 에 넣거나 converse_json 을 잠깐 바꿔 끼운다.
"""
import ai.extract as ex
from ai.bedrock import ConverseResult


def llm(raw, normalized, type_, evidence):
    return {"raw": raw, "normalized": normalized, "type": type_, "evidence": evidence}


def view(skills):
    return [(s["raw"], s["normalized"], s["confidence"], s["section"]) for s in skills]


def demo():
    text = "[자격요건]\n쿠버네티스(EKS) 경험\n[우대사항]\nZabbix와 Foo 사용 경험"

    # Kubernetes 와 EKS 를 LLM 이 두 항목으로 내면 둘 다 사전에 있어 high, 근거가 [자격요건] 아래라 qualification
    got = ex.to_contract([llm("쿠버네티스", "Kubernetes", "required", "쿠버네티스(EKS) 경험"),
                          llm("EKS", "EKS", "required", "쿠버네티스(EKS) 경험")], text, "v2.1")
    assert view(got) == [("쿠버네티스", "Kubernetes", "high", "qualification"),
                         ("EKS", "EKS", "high", "qualification")], view(got)

    # 주의: LLM 이 괄호째 한 항목으로 내면 코드는 나누지 않는다. normalized 로 대표명이 하나 정해지고 EKS 는 생기지 않는다.
    # 괄호 안의 다른 기술을 따로 쓰는 것은 프롬프트(v2.1)의 몫이다
    got = ex.to_contract([llm("쿠버네티스(EKS)", "Kubernetes", "required", "쿠버네티스(EKS) 경험")], text, "v2.1")
    assert view(got) == [("쿠버네티스(EKS)", "Kubernetes", "high", "qualification")], view(got)
    # raw 와 normalized 가 모두 괄호째면 사전에 없어(#76 에서 별칭 제거) uncertain
    got = ex.to_contract([llm("쿠버네티스(EKS)", "쿠버네티스(EKS)", "required", "쿠버네티스(EKS) 경험")], text, "v2.1")
    assert [s["confidence"] for s in got] == ["uncertain"]

    # 사전 밖 이름은 근거가 있어도 uncertain
    got = ex.to_contract([llm("Foo", "Foo", "preferred", "Zabbix와 Foo 사용 경험")], text, "v2.1")
    assert view(got) == [("Foo", "Foo", "uncertain", "preference")], view(got)

    # 같은 기술이 두 칸에 나오면 칸 기준 가중치가 큰 쪽 하나만: 주요업무(0.7) > 우대사항(0.5). type 이 같아도 칸이 이긴다
    text2 = "[주요업무]\nAWS 운영 자동화\n[우대사항]\nAWS 자격증 보유 우대"
    got = ex.to_contract([llm("AWS", "AWS", "required", "AWS 자격증 보유 우대"),
                          llm("AWS", "AWS", "required", "AWS 운영 자동화")], text2, "v2.1")
    assert view(got) == [("AWS", "AWS", "high", "duty")], view(got)

    # 근거 문장이 본문에 없으면 uncertain(칸도 못 찾아 None), 근거가 비면 버린다
    got = ex.to_contract([llm("Python", "Python", "required", "본문에 없는 문장"),
                          llm("Go", "Go", "required", "  ")], text, "v2.1")
    assert view(got) == [("Python", "Python", "uncertain", None)], view(got)

    # PROMPTS 에 없는 버전은 KeyError 나 다른 버전으로 조용히 도는 대신 ValueError
    for call in (lambda: ex.to_contract([], text, "v9"), lambda: ex.extract_with_details({"id": "x", "text": text}, "v9")):
        try:
            call()
            raise AssertionError("없는 버전인데 오류가 나지 않았습니다")
        except ValueError as e:
            assert "지원하지 않는 프롬프트 버전" in str(e), e

    # ncs_unit: v2 이후는 마지막에 standardize 를 불러 사전에 있는 기술만 확신 있는 NCS 단위를 채운다(S3 는 있음, Docker 는 없음)
    original = ex.converse_json
    try:
        ex.converse_json = lambda *a, **k: ConverseResult(
            {"skills": [llm("S3", "S3", "required", "S3 운영 경험"), llm("도커", "도커", "required", "도커 사용")]}, 1, {})
        posting = {"id": "x", "text": "S3 운영 경험\n도커 사용"}
        extraction, _, _ = ex.extract_with_details(posting, "v2.1")
        assert [(s["normalized"], s["ncs_unit"]) for s in extraction["skills"]] == [("S3", "2001021507"), ("Docker", None)]
        # v1 은 standardize 를 부르지 않는다(이름도 맞추지 않고 ncs_unit 도 비어 있음)
        extraction, _, _ = ex.extract_with_details(posting, "v1")
        assert [(s["normalized"], s["ncs_unit"]) for s in extraction["skills"]] == [("S3", None), ("도커", None)]
    finally:
        ex.converse_json = original


if __name__ == "__main__":
    demo()
    print("통과")
