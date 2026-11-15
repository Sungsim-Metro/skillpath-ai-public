"""추출 결과(②)의 기술 이름을 별칭 사전으로 표준화한다 (#13). 근거 검증 다음, 저장 이전에 부른다.

- 사전에 있으면 normalized 를 대표 이름으로 덮어쓴다(LLM 이 붙인 이름보다 사전을 믿는다).
- 사전에 없으면 normalized 는 그대로 두고 confidence 를 "uncertain" 으로 낮춘다. 합치지 않는다.
  그 표현은 unknown 목록으로 돌려줘 사람이 사전에 더할지 정한다.
- 사전에 있는 기술은 ncs_unit 도 채운다(확신이 있는 연결만, 없으면 None 그대로).
- 이름을 먼저 맞추고, 그다음에 같은 기술을 합친다. 합칠 때는 팀 규칙대로 가중치가 가장 큰 것 하나를 남긴다
  (ai.sections.weight: 칸이 있으면 칸 기준, 없으면 type 기준. 같으면 먼저 나온 것).
  이름을 맞추면서 새로 생긴 중복에도 같은 규칙이 먹는다.
- 입력은 고치지 않는다. 숫자를 세거나 LLM 을 부르지 않는다.
"""
from __future__ import annotations

from ai.sections import weight
from pipeline.aliases import normalize_skill
from pipeline.ncs import ncs_unit


def standardize(extraction: dict) -> tuple[dict, list[str]]:
    """(표준화한 추출 결과, 사전에 없는 표현 목록). 형식은 contracts/ ② 그대로다."""
    by_name: dict[str, dict] = {}
    unknown: list[str] = []
    for skill in extraction["skills"]:
        canon = normalize_skill(skill["raw"]) or normalize_skill(skill["normalized"])
        item = dict(skill)
        if canon:
            item["normalized"] = canon
            if item.get("ncs_unit") is None:  # NCS 단위는 확신이 있을 때만 채운다(pipeline/ncs.py)
                item["ncs_unit"] = ncs_unit(canon)
        else:
            item["confidence"] = "uncertain"
            if skill["raw"] not in unknown:
                unknown.append(skill["raw"])
        prev = by_name.get(item["normalized"])
        if prev is None or weight(item["type"], item.get("section")) > weight(prev["type"], prev.get("section")):
            by_name[item["normalized"]] = item
    return {**extraction, "skills": list(by_name.values())}, unknown
