"""채용공고(①) → 추출 결과(②). (#9)

LLM 은 raw, normalized, type, evidence 만 낸다. posting_id, confidence, ncs_unit 은 코드가 채운다.
- evidence 가 비면 버린다.
- evidence 가 공고에 없거나, normalized 가 대표명 목록에 없으면 confidence = "uncertain".
"""
from __future__ import annotations

from ai.bedrock import ConverseResult, converse_json
from ai.evidence import evidence_in_text
from ai.prompts import extract_v1 as prompt

PROMPT_VERSION = prompt.VERSION
MOCK_FILE = "mock_bedrock_extract.json"
TYPES = ("required", "preferred")


def validate_llm_output(data: dict) -> None:
    """LLM 출력 형식 검사. 틀리면 ValueError (converse_json 이 한 번 재시도한다)."""
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise ValueError('"skills" 목록이 없습니다')
    for i, s in enumerate(skills):
        if not isinstance(s, dict):
            raise ValueError(f"skills[{i}] 가 객체가 아닙니다")
        for key in ("raw", "normalized", "type", "evidence"):
            if not isinstance(s.get(key), str):
                raise ValueError(f"skills[{i}].{key} 가 문자열이 아닙니다")
        if s["type"] not in TYPES:
            raise ValueError(f"skills[{i}].type 이 {s['type']!r} 입니다")


def to_contract(llm_skills: list[dict], text: str) -> list[dict]:
    """LLM 출력을 ② skills 형식으로 바꾼다. 같은 기술이 둘이면 required 쪽을 남긴다."""
    by_name: dict[str, dict] = {}
    for s in llm_skills:
        name = s["normalized"].strip()
        evidence = s["evidence"].strip()
        if not name or not evidence:
            continue  # 근거 없는 결과는 내보내지 않는다
        found = evidence_in_text(evidence, text)
        item = {
            "raw": s["raw"].strip(),
            "normalized": name,
            "type": s["type"],
            "evidence": evidence,
            "confidence": "high" if found and name in prompt.CANONICAL_SKILLS else "uncertain",
            "ncs_unit": None,
        }
        prev = by_name.get(name)
        if prev is None or (prev["type"] == "preferred" and item["type"] == "required"):
            by_name[name] = item
    return list(by_name.values())


def extract_with_meta(posting: dict) -> tuple[dict, ConverseResult]:
    """평가용. 추출 결과와 함께 시도 횟수·토큰 사용량을 돌려준다."""
    result = converse_json(
        prompt.SYSTEM,
        prompt.user_message(posting["text"]),
        mock_file=MOCK_FILE,
        mock_key=posting["id"],
        validate=validate_llm_output,
    )
    extraction = {
        "posting_id": posting["id"],  # ②의 posting_id 는 ①의 id 를 가리킨다
        "skills": to_contract(result.data["skills"], posting["text"]),
    }
    return extraction, result


def extract_skills(posting: dict) -> dict:
    """공고 ① 를 받아 추출 결과 ② 를 돌려준다."""
    return extract_with_meta(posting)[0]
