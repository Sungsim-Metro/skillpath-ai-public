"""화면(app)이 부르는 함수 모음.

지금(M1)은 BEDROCK_MOCK=1 이면 data/sample 의 가짜 데이터를 돌려준다.
함수 이름과 반환 형식은 그대로 두고, 담당자가 실제 구현으로 교체한다.

| 함수                 | 반환 형식           | 실제 구현 담당          |
| -------------------- | ------------------- | ----------------------- |
| get_demand           | ④ 수요 집계          | 일현 (#12)              |
| get_gap              | Gap 목록            | 일현 (#12)              |
| get_area_gap         | 영역별 Gap 목록      | 일현 (#12, #17)         |
| get_market_gap       | 시장 Gap 표          | 담당 미정 (⑤ 공급 데이터) |
| get_recommendations  | ③ 추천 목록          | 종훈·성진 (#14, #15)     |
| extract_posting      | ② 추출 결과          | 성진·일현 (#9, #11)      |

형식은 contracts/README.md 를 따른다.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLE_DIR = DATA_DIR / "sample"

UNCLASSIFIED = "미분류"
# 시장 Gap 의 구분별 가중치(5주차 실습 값). 주요 업무(duty) 건수는 ④에 필드가 생기면(#52) 함께 센다.
SECTION_WEIGHT = {"required": 1.0, "duty": 0.7, "preferred": 0.5}

# 가짜 모드에서만 쓰는 최소 별칭 표. 실제 표준화는 pipeline 의 별칭 사전(#13)이 맡는다.
_MOCK_ALIASES = {
    "아마존 웹 서비스": "AWS",
    "리눅스": "Linux",
    "파이썬": "Python",
    "도커": "Docker",
    "쿠버네티스": "Kubernetes",
    "EKS": "Kubernetes",
    "IaC": "Terraform",
}


def is_mock() -> bool:
    return os.getenv("BEDROCK_MOCK", "1") == "1"


def _load(name: str):
    return json.loads((SAMPLE_DIR / name).read_text(encoding="utf-8"))


def get_demand(job_category: str) -> dict:
    """직무의 기술별 수요 집계(④)를 돌려준다."""
    if is_mock():
        return _load("demand.json")
    raise NotImplementedError("실제 수요 집계는 #12 에서 구현합니다.")


@lru_cache
def _skill_areas() -> dict:
    """표준 기술명 → 영역 사전(data/skill_areas.json). 배정은 사람이 정하고 코드는 조회만 한다."""
    return json.loads((DATA_DIR / "skill_areas.json").read_text(encoding="utf-8"))


def _area_of(skill: str) -> tuple[str, str]:
    """(영역 id, 영역 이름). 사전에 없으면 미분류."""
    table = _skill_areas()
    area_id = table["skills"].get(skill)
    return (area_id, table["areas"][area_id]) if area_id else ("UNCLASSIFIED", UNCLASSIFIED)


def get_gap(job_category: str, owned_skills: list[str], min_ratio: float = 0.2) -> list[dict]:
    """공고 비율이 min_ratio 이상인 기술을 수요 높은 순으로 돌려주고, 보유 여부를 표시한다.

    계산은 전부 코드로 한다. LLM 을 쓰지 않는다.
    """
    demand = get_demand(job_category)
    total = demand["total_postings"]
    owned = {s.lower() for s in owned_skills}
    rows = []
    for s in demand["skills"]:
        ratio = s["postings"] / total if total else 0
        if ratio < min_ratio:
            continue
        rows.append({
            "skill": s["skill"],
            "area": _area_of(s["skill"])[1],
            "postings": s["postings"],
            "of": total,
            "ratio": round(ratio, 2),
            "required_ratio": round(s["required"] / s["postings"], 2) if s["postings"] else 0,
            "owned": s["skill"].lower() in owned,
        })
    rows.sort(key=lambda r: r["postings"], reverse=True)
    return rows


def get_area_gap(job_category: str, owned_skills: list[str]) -> list[dict]:
    """영역(능력 범주)마다 공고 수요와 그중 내가 가진 몫을 돌려준다. 계산은 전부 코드로 한다.

    - demand: 그 영역 기술들의 요구 공고 수를 더한 값. 한 공고가 영역의 기술 둘을 요구하면 2로 센다.
    - owned: 그중 보유 기술의 요구 공고 수 합. coverage = owned / demand (demand 가 0이면 0).
    영역 순서는 data/skill_areas.json 의 순서이고, 사전에 없는 기술은 마지막의 미분류에 모은다.
    """
    demand = get_demand(job_category)
    owned = {s.lower() for s in owned_skills}
    rows = {
        area_id: {"area_id": area_id, "area": name, "demand": 0, "owned": 0, "skills": [], "missing_skills": []}
        for area_id, name in [*_skill_areas()["areas"].items(), ("UNCLASSIFIED", UNCLASSIFIED)]
    }
    for s in demand["skills"]:
        row = rows[_area_of(s["skill"])[0]]
        row["demand"] += s["postings"]
        row["skills"].append(s["skill"])
        if s["skill"].lower() in owned:
            row["owned"] += s["postings"]
        else:
            row["missing_skills"].append(s["skill"])
    for row in rows.values():
        row["coverage"] = round(row["owned"] / row["demand"], 2) if row["demand"] else 0
    return list(rows.values())


def _get_supply() -> dict:
    """교육 공급 집계(⑤): 기술별로 그 기술을 다루는 교과목 수."""
    if is_mock():
        return _load("supply.json")
    raise NotImplementedError("실제 교과목 공급 집계는 데이터와 담당이 정해지면 구현합니다.")


def _shares(rates: dict[str, float]) -> dict[str, float]:
    """{기술: 값} → {기술: 전체 합에서 차지하는 비중(0~1)}"""
    total = sum(rates.values()) or 1.0
    return {k: v / total for k, v in rates.items()}


def get_market_gap(job_category: str) -> dict:
    """시장 Gap: 공고는 원하는데 교육이 덜 다루는 기술. 5주차 실습 정의를 그대로 따른다.

    - 수요율 = 가중치 합 / 공고 수. 공고 1건은 기술마다 가장 강한 구분의 가중치 하나로 센다(SECTION_WEIGHT).
    - 공급율 = 그 기술을 다루는 교과목 수 / 전체 교과목 수
    - 비중 = 각 값 / 전체 합. Gap(%p) = (수요 비중 - 공급 비중) × 100
    + 이면 시장은 원하는데 교육이 덜 다루는 기술, - 이면 교육이 상대적으로 많이 다루는 기술이다.
    rows 는 Gap 이 큰 순서. 계산은 전부 코드로 한다.
    """
    demand = get_demand(job_category)
    supply = _get_supply()
    total = demand["total_postings"]
    demand_rate = {
        s["skill"]: sum(w * s.get(kind, 0) for kind, w in SECTION_WEIGHT.items()) / total if total else 0
        for s in demand["skills"]
    }
    courses = supply["total_courses"]
    supply_rate = {s["skill"]: s["courses"] / courses if courses else 0 for s in supply["skills"]}
    d_share, s_share = _shares(demand_rate), _shares(supply_rate)
    rows = []
    for skill in sorted(set(d_share) | set(s_share)):
        d, p = d_share.get(skill, 0) * 100, s_share.get(skill, 0) * 100
        rows.append({"skill": skill, "demand_pct": round(d, 1), "supply_pct": round(p, 1), "gap_pp": round(d - p, 1)})
    rows.sort(key=lambda r: r["gap_pp"], reverse=True)
    return {
        "weights": dict(SECTION_WEIGHT),
        "total_postings": total,
        "supply": {k: supply.get(k) for k in ("source", "total_courses", "note")},
        "rows": rows,
    }


def get_recommendations(job_category: str, gap_skills: list[str]) -> list[dict]:
    """부족 기술마다 추천(③)을 돌려준다. 찾은 과정이 없으면 courses 가 빈 목록이다."""
    if not is_mock():
        raise NotImplementedError("실제 추천은 #14, #15 에서 구현합니다.")
    by_skill = {r["gap_skill"]: r for r in _load("recommendations.json")}
    demand = {s["skill"]: s for s in get_demand(job_category)["skills"]}
    total = get_demand(job_category)["total_postings"]
    out = []
    for skill in gap_skills:
        if skill in by_skill:
            out.append(by_skill[skill])
            continue
        d = demand.get(skill, {"postings": 0, "required": 0})
        out.append({
            "gap_skill": skill,
            "demand": {
                "postings": d["postings"],
                "of": total,
                "required_ratio": round(d["required"] / d["postings"], 2) if d["postings"] else 0,
            },
            "courses": [],
        })
    return out


def extract_posting(text: str, posting_id: str = "manual-input") -> dict:
    """공고 본문에서 기술, 필수/우대, 근거 문장을 뽑는다(②).

    가짜 모드에서는 키워드를 찾아 그 구절을 근거로 쓴다. 실제 모드는 ai.extract(#9)의 Bedrock 추출을 부른다.
    """
    if not is_mock():
        from ai.extract import extract_skills

        return extract_skills({"id": posting_id, "text": text})
    vocab = {s["skill"]: s["skill"] for s in _load("demand.json")["skills"]}
    vocab.update(_MOCK_ALIASES)
    clauses = [c.strip() for c in re.split(r"[.\n,·;]", text) if c.strip()]
    skills, seen = [], set()
    for clause in clauses:
        for raw, normalized in vocab.items():
            if raw.lower() in clause.lower() and normalized not in seen:
                seen.add(normalized)
                skills.append({
                    "raw": raw,
                    "normalized": normalized,
                    "type": "preferred" if "우대" in clause else "required",
                    "evidence": clause,
                    "confidence": "high" if raw == normalized else "uncertain",
                    "ncs_unit": None,
                })
    return {"posting_id": posting_id, "skills": skills}
