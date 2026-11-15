"""화면(app)이 부르는 함수 모음.

지금(M1)은 BEDROCK_MOCK=1 이면 data/sample 의 가짜 데이터를 돌려준다.
함수 이름과 반환 형식은 그대로 두고, 담당자가 실제 구현으로 교체한다.

| 함수                 | 반환 형식           | 실제 구현 담당          |
| -------------------- | ------------------- | ----------------------- |
| get_demand           | ④ 수요 집계          | 일현 (#12)              |
| get_gap              | Gap 목록            | 일현 (#12)              |
| get_recommendations  | ③ 추천 목록          | 종훈·성진 (#14, #15)     |
| extract_posting      | ② 추출 결과          | 성진·일현 (#9, #11)      |

형식은 contracts/README.md 를 따른다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"

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
            "postings": s["postings"],
            "of": total,
            "ratio": round(ratio, 2),
            "required_ratio": round(s["required"] / s["postings"], 2) if s["postings"] else 0,
            "owned": s["skill"].lower() in owned,
        })
    rows.sort(key=lambda r: r["postings"], reverse=True)
    return rows


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

        return extract_skills({"posting_id": posting_id, "text": text})
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
