"""기술 → NCS 능력단위 (#20). ②의 skills[].ncs_unit 값(능력단위 분류번호)을 정한다.

기술 → 세부 능력(pipeline.search.subskills_of) → NCS 능력단위(data/ncs_subskill.json).
- 세부 능력이 하나로 정해지고 연결이 certain 일 때만 값을 낸다. 아니면 None 이다.
  (NCS 문장에는 도구 이름이 거의 없어 그럴듯하게 채우면 틀린 연결이 생긴다. 확신이 없으면 비운다.)
- 임베딩 기반 매칭(hit@5)은 이 함수 안쪽을 바꿔 M3 에서 얹는다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pipeline.search import subskills_of

DATA = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def _links() -> dict[str, dict]:
    return json.loads((DATA / "ncs_subskill.json").read_text(encoding="utf-8"))["links"]


@lru_cache(maxsize=1)
def _units() -> dict[str, dict]:
    return {u["code"]: u for u in json.loads((DATA / "ncs_units.json").read_text(encoding="utf-8"))["units"]}


def ncs_unit(skill: str) -> str | None:
    """기술의 NCS 능력단위 분류번호. 맞는 단위가 없거나 확신이 없으면 None."""
    subskills = subskills_of(skill)
    if len(subskills) != 1:  # 세부 능력이 없거나 여럿이면 어느 단위인지 정할 수 없다
        return None
    link = _links().get(subskills[0])
    return link["unit"] if link and link["confidence"] == "certain" else None


def ncs_name(code: str) -> str | None:
    """분류번호 → 능력단위 이름(화면에 보여줄 때)."""
    unit = _units().get(code)
    return unit["name"] if unit else None
