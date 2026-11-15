"""채용공고(①) → 추출 결과(②). (#9)

LLM 은 raw, normalized, type, evidence 만 낸다. posting_id, section, confidence, ncs_unit 은 코드가 채운다.
- evidence 가 비면 버린다.
- evidence 가 공고에 없거나, normalized 가 대표명 목록에 없으면 confidence = "uncertain".
- section 은 근거 문장이 들어 있는 칸(ai.sections). 혜택·회사소개 같은 제외 칸에서 나온 기술은 버린다.
- 같은 기술이 여러 번 나오면 수요 가중치(ai.sections.weight)가 가장 큰 것 하나만 남긴다.
"""
from __future__ import annotations

from ai.bedrock import ConverseResult, converse_json
from ai.evidence import evidence_in_text
from ai.prompts import extract_v1 as prompt
from ai.sections import EXCLUDED, find_section, weight

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


def to_contract_with_dropped(llm_skills: list[dict], text: str) -> tuple[list[dict], list[dict]]:
    """LLM 출력을 ② skills 형식으로 바꾼다. (② skills, 제외 칸이라 버린 LLM 출력) 을 돌려준다."""
    by_name: dict[str, dict] = {}
    dropped: list[dict] = []
    for s in llm_skills:
        name = s["normalized"].strip()
        evidence = s["evidence"].strip()
        if not name or not evidence:
            continue  # 근거 없는 결과는 내보내지 않는다
        section = find_section(text, evidence)
        if section == EXCLUDED:
            dropped.append(s)  # 혜택·회사소개 칸은 지원자에게 요구하는 기술이 아니다
            continue
        found = evidence_in_text(evidence, text)
        item = {
            "raw": s["raw"].strip(),
            "normalized": name,
            "type": s["type"],
            "section": section,
            "evidence": evidence,
            "confidence": "high" if found and name in prompt.CANONICAL_SKILLS else "uncertain",
            "ncs_unit": None,
        }
        prev = by_name.get(name)
        if prev is None or weight(item["type"], item["section"]) > weight(prev["type"], prev["section"]):
            by_name[name] = item
    return list(by_name.values()), dropped


def to_contract(llm_skills: list[dict], text: str) -> list[dict]:
    """LLM 출력을 ② skills 형식으로 바꾼다."""
    return to_contract_with_dropped(llm_skills, text)[0]


def extract_with_details(posting: dict) -> tuple[dict, ConverseResult, list[dict]]:
    """채점용. (추출 결과 ②, 시도 횟수·토큰 사용량, 제외 칸이라 버린 LLM 출력)."""
    result = converse_json(
        prompt.SYSTEM,
        prompt.user_message(posting["text"]),
        mock_file=MOCK_FILE,
        mock_key=posting["id"],
        validate=validate_llm_output,
    )
    skills, dropped = to_contract_with_dropped(result.data["skills"], posting["text"])
    extraction = {
        "posting_id": posting["id"],  # ②의 posting_id 는 ①의 id 를 가리킨다
        "skills": skills,
    }
    return extraction, result, dropped


def extract_with_meta(posting: dict) -> tuple[dict, ConverseResult]:
    """평가용. 추출 결과와 함께 시도 횟수·토큰 사용량을 돌려준다."""
    extraction, result, _ = extract_with_details(posting)
    return extraction, result


def extract_skills(posting: dict) -> dict:
    """공고 ① 를 받아 추출 결과 ② 를 돌려준다."""
    return extract_with_meta(posting)[0]
