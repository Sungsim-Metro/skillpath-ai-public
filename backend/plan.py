"""진단 보고서의 수강 순서 (2026-11-19, #98). 계산은 전부 코드로 한다. LLM 을 쓰지 않는다(원칙 2).

- 받은 추천(backend.api.get_recommendations 결과)의 순서를 그대로 출발점으로 쓴다. 기술은 수요 높은 순,
  기술 안의 과정은 내 단계에 맞는 난이도가 앞이다(api._with_level).
- "불확실" 과정은 순서에 넣지 않는다(원칙 4). 보고서가 따로 적는다.
- 같은 과정(같은 주소)이 여러 기술에 추천되면 처음 한 번만 넣는다.
- 선수 보정: 과정의 선수지식 글에 다른 부족 기술 이름이 있으면(예: EKS 입문의 "Docker"), 그 기술의 과정 뒤로 미룬다.
  서로를 선수로 걸어 순서를 정할 수 없으면 원래 순서대로 둔다.
소요 시간은 순서를 정하는 데 쓰지 않는다(11/19 결정: 순서만). 화면에 보여 줄 합계만 plan_minutes 가 낸다(2026-11-21).
과정 표에 소요 시간이 채워졌고(216개 전부), 비어 있는 과정은 합에서 빼고 그 수를 함께 돌려준다.
"""
from __future__ import annotations

import re


def _mentions(text: str, skill: str) -> bool:
    """선수지식 글에 기술 이름이 단어로 들어 있는가. 대소문자를 가리지 않는다("Go"가 "Google"에 걸리지 않게)."""
    return bool(re.search(rf"(?<![0-9A-Za-z]){re.escape(skill)}(?![0-9A-Za-z])", text or "", re.IGNORECASE))


def study_order(recommendations: list[dict]) -> list[dict]:
    """[{"step", "skill", "title", "url", "level", "after"}] 를 들을 순서대로 돌려준다.

    after 는 선수 보정으로 이 과정을 뒤로 미루게 한 기술 이름 목록이다(없으면 빈 목록).
    과정에 소요 시간(③ 의 선택 필드 duration_min)이 있으면 그 값도 함께 싣는다.
    """
    items, seen = [], set()
    for rec in recommendations:
        for c in rec["courses"]:
            key = c.get("url") or c["title"]
            if c.get("match") == "uncertain" or key in seen:
                continue
            seen.add(key)
            items.append({"skill": rec["gap_skill"], "title": c["title"], "url": c.get("url", ""),
                          "level": c.get("level", ""), "prerequisites": c.get("prerequisites", ""),
                          **({"duration_min": c["duration_min"]} if _minutes(c.get("duration_min")) else {})})
    skills = {it["skill"] for it in items}
    for it in items:
        it["after"] = [s for s in sorted(skills) if s != it["skill"] and _mentions(it["prerequisites"], s)]

    ordered, rest = [], list(items)
    while rest:
        placed = {it["skill"] for it in ordered}
        pending = {it["skill"] for it in rest}
        # 선수 기술의 과정이 아직 남아 있지 않은 첫 과정을 고른다. 모두 막혀 있으면(서로 선수) 맨 앞 과정
        ready = next((it for it in rest if all(s in placed or s not in pending for s in it["after"])), rest[0])
        rest.remove(ready)
        ordered.append(ready)
    return [{"step": i, "skill": it["skill"], "title": it["title"], "url": it["url"], "level": it["level"],
             "after": it["after"], **({"duration_min": it["duration_min"]} if "duration_min" in it else {})}
            for i, it in enumerate(ordered, start=1)]


def _minutes(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def plan_minutes(plan: list[dict]) -> tuple[int, int]:
    """(수강 순서에 든 과정의 소요 시간 합(분), 소요 시간이 없는 과정 수). 합은 화면이 아니라 여기서 낸다(원칙 2)."""
    minutes = [_minutes(item.get("duration_min")) for item in plan]
    return sum(minutes), sum(1 for m in minutes if not m)


def uncovered_skills(recommendations: list[dict]) -> list[dict]:
    """확실한 과정을 찾지 못한 추천 기술: [{"skill", "status"}]. status 는 "none"(과정 없음) 또는 "uncertain"(관련 과정만)."""
    out = []
    for rec in recommendations:
        if any(c.get("match") != "uncertain" for c in rec["courses"]):
            continue
        out.append({"skill": rec["gap_skill"], "status": "uncertain" if rec["courses"] else "none"})
    return out
