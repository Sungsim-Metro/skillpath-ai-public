"""채용공고(①) → 추출 결과(②). (#9, #21)

LLM 은 raw, normalized, type, evidence 만 낸다. posting_id, section, confidence, ncs_unit 은 코드가 채운다.
- evidence 가 비면 버린다.
- section 은 근거 문장이 들어 있는 칸(ai.sections). 혜택·회사소개 같은 제외 칸에서 나온 기술은 버린다.
- 같은 기술이 여러 번 나오면 수요 가중치(ai.sections.weight)가 가장 큰 것 하나만 남긴다.

프롬프트 버전 (기본은 v1. 기본값을 v2.1 로 바꾸는 시점은 #21 채점 뒤에 정한다)
- v1: confidence 는 근거가 공고에 있고 normalized 가 v1 대표명 12개 안이면 high
- v2: 이름을 팀 별칭 사전(pipeline.aliases.normalize_skill)으로 대표명에 맞춘 **뒤에** 중복을 합친다.
      confidence 는 근거가 공고에 있고 사전에 있는 기술이면 high. 마지막에 pipeline.standardize 로 ncs_unit 을 채운다.
- v2.1: 프롬프트만 다르고(종류 이름·괄호 별칭·'/' 나열 규칙, 정답지와 겹치지 않는 예시) 후처리는 v2 와 같다.
        #21 채점용 공고로 판단할 버전이다.
PROMPTS 에 없는 버전을 주면 ValueError 를 낸다.
"""
from __future__ import annotations

from ai.bedrock import ConverseResult, converse_json
from ai.evidence import evidence_in_text
from ai.prompts import extract_v1, extract_v2, extract_v2_1
from ai.sections import EXCLUDED, find_section, weight
from pipeline.aliases import normalize_skill

PROMPTS = {"v1": extract_v1, "v2": extract_v2, "v2.1": extract_v2_1}  # v2.1 의 후처리는 v2 와 같다
DEFAULT_PROMPT = "v1"
PROMPT_VERSION = PROMPTS[DEFAULT_PROMPT].VERSION
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


def prompt_of(prompt_version: str):
    """버전 이름 → 프롬프트 모듈. 없는 버전이면 다른 버전으로 조용히 돌지 않게 ValueError."""
    if prompt_version not in PROMPTS:
        raise ValueError(f"지원하지 않는 프롬프트 버전: {prompt_version!r} (가능: {', '.join(PROMPTS)})")
    return PROMPTS[prompt_version]


def _canonical(s: dict, prompt_version: str) -> tuple[str, bool]:
    """(② 에 쓸 이름, 대표명 목록·사전 안의 기술인가)."""
    name = s["normalized"].strip()
    if prompt_version == "v1":
        return name, name in extract_v1.CANONICAL_SKILLS
    canon = normalize_skill(s["raw"]) or normalize_skill(name)  # pipeline.standardize 와 같은 순서
    return (canon, True) if canon else (name, False)


def to_contract_with_dropped(
    llm_skills: list[dict], text: str, prompt_version: str = DEFAULT_PROMPT
) -> tuple[list[dict], list[dict]]:
    """LLM 출력을 ② skills 형식으로 바꾼다. (② skills, 제외 칸이라 버린 LLM 출력) 을 돌려준다."""
    prompt_of(prompt_version)  # 없는 버전이면 여기서 멈춘다 (v1 이 아니라는 이유로 v2 후처리로 가지 않게)
    by_name: dict[str, dict] = {}
    dropped: list[dict] = []
    for s in llm_skills:
        name, known = _canonical(s, prompt_version)
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
            "confidence": "high" if found and known else "uncertain",
            "ncs_unit": None,
        }
        prev = by_name.get(name)
        if prev is None or weight(item["type"], item["section"]) > weight(prev["type"], prev["section"]):
            by_name[name] = item
    return list(by_name.values()), dropped


def to_contract(llm_skills: list[dict], text: str, prompt_version: str = DEFAULT_PROMPT) -> list[dict]:
    """LLM 출력을 ② skills 형식으로 바꾼다."""
    return to_contract_with_dropped(llm_skills, text, prompt_version)[0]


def extract_with_details(
    posting: dict, prompt_version: str = DEFAULT_PROMPT
) -> tuple[dict, ConverseResult, list[dict]]:
    """채점용. (추출 결과 ②, 시도 횟수·토큰 사용량, 제외 칸이라 버린 LLM 출력)."""
    prompt = prompt_of(prompt_version)
    result = converse_json(
        prompt.SYSTEM,
        prompt.user_message(posting["text"]),
        mock_file=MOCK_FILE,
        mock_key=posting["id"],
        validate=validate_llm_output,
    )
    skills, dropped = to_contract_with_dropped(result.data["skills"], posting["text"], prompt_version)
    extraction = {
        "posting_id": posting["id"],  # ②의 posting_id 는 ①의 id 를 가리킨다
        "skills": skills,
    }
    if prompt_version != "v1":
        from pipeline.standardize import standardize

        extraction, _ = standardize(extraction)  # 이름은 이미 대표명이라 ncs_unit 만 채워진다
    return extraction, result, dropped


def extract_with_meta(posting: dict, prompt_version: str = DEFAULT_PROMPT) -> tuple[dict, ConverseResult]:
    """평가용. 추출 결과와 함께 시도 횟수·토큰 사용량을 돌려준다."""
    extraction, result, _ = extract_with_details(posting, prompt_version)
    return extraction, result


def extract_skills(posting: dict, prompt_version: str = DEFAULT_PROMPT) -> dict:
    """공고 ① 를 받아 추출 결과 ② 를 돌려준다."""
    return extract_with_meta(posting, prompt_version)[0]
