"""선택한 최신 성공 추출 결과를 고유 공고 기준으로 다시 집계한다."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from backend.jobs import JOBS

AGGREGATION_VERSION = "demand-v1"


def _posting_skills(item: dict) -> list[dict]:
    """중복 기술은 한 번만 센다. 검증을 통과한 필수 후보를 우선한다."""
    extraction = item["extraction"]
    if extraction.get("posting_id") != item["posting_id"]:
        raise ValueError("공고 키와 추출 결과의 posting_id가 다릅니다.")
    if not isinstance(extraction.get("skills"), list):
        raise ValueError("저장된 추출 결과에 skills 목록이 없습니다.")
    by_name = {}
    for skill in extraction["skills"]:
        if not isinstance(skill, dict):
            raise ValueError("저장된 기술 결과가 객체가 아닙니다.")
        if skill.get("confidence") not in ("high", "uncertain"):
            raise ValueError("저장된 기술 결과의 confidence가 잘못되었습니다.")
        if skill.get("type") not in ("required", "preferred"):
            raise ValueError("저장된 기술 결과의 type이 잘못되었습니다.")
        if not all(isinstance(skill.get(key), str) and skill[key].strip()
                   for key in ("normalized", "evidence")):
            raise ValueError("저장된 기술 결과의 표준명이나 근거가 비어 있습니다.")
        name = skill["normalized"]
        rank = (
            skill["confidence"] != "high",
            skill["type"] != "required",
            skill["evidence"],
            skill.get("raw", ""),
        )
        previous = by_name.get(name)
        if previous is None or rank < previous[0]:
            by_name[name] = (rank, skill)
    return [by_name[name][1] for name in sorted(by_name)]


def build_snapshot(
    items: list[dict],
    *,
    selected: dict[str, str],
    job_category: str,
    collected: str,
    exclusions: dict[str, str],
    selection_version: str,
    run_id: str,
) -> dict:
    """이번에 선택한 입력의 성공 결과가 모두 있을 때 완성 스냅샷을 만든다."""
    if job_category not in JOBS:
        raise ValueError(f"지원하지 않는 서비스 직무 코드: {job_category}")
    if not collected or not selection_version or not run_id:
        raise ValueError("수집일, 선정 규칙 버전, 실행 ID가 필요합니다.")
    if set(selected) & set(exclusions):
        raise ValueError("제외한 공고가 집계 대상에도 들어 있습니다.")
    if any(not isinstance(reason, str) or not reason.strip() for reason in exclusions.values()):
        raise ValueError("공고 제외 사유가 비어 있습니다.")

    records = {}
    for item in items:
        posting_id = item.get("posting_id")
        if (item.get("job_category") != job_category or posting_id not in selected
                or item.get("input_fingerprint") != selected[posting_id]):
            continue
        previous = records.get(posting_id)
        if previous is not None and previous != item:
            raise ValueError(f"같은 공고 입력의 성공 결과가 서로 다릅니다: {posting_id}")
        records[posting_id] = item
    missing = sorted(set(selected) - set(records))
    if missing:
        raise ValueError(f"선택한 입력의 성공 결과가 없는 공고: {', '.join(missing)}")

    skills = {}
    uncertain_counts = Counter()
    empty_count = uncertain_only_count = 0
    sources = set()
    for posting_id in sorted(records):
        item = records[posting_id]
        sources.add(item["source"])
        posting_skills = _posting_skills(item)
        empty_count += not posting_skills
        uncertain_only_count += bool(posting_skills) and all(
            skill["confidence"] == "uncertain" for skill in posting_skills
        )
        for skill in posting_skills:
            name = skill["normalized"]
            if skill["confidence"] == "uncertain":
                uncertain_counts[name] += 1
                continue
            row = skills.setdefault(name, {
                "skill": name, "postings": 0, "required": 0,
                "preferred": 0, "evidence_samples": [],
            })
            row["postings"] += 1
            row[skill["type"]] += 1
            if len(row["evidence_samples"]) < 3:
                row["evidence_samples"].append({
                    "posting_id": posting_id,
                    "url": item["posting_meta"].get("url"),
                    "evidence": skill["evidence"],
                })

    return {
        "job_category": job_category,
        "snapshot_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "demand": {
            "job_category": job_category,
            "job_title": JOBS[job_category],
            "total_postings": len(records),
            "sources": sorted(sources),
            "collected": collected,
            "skills": sorted(skills.values(), key=lambda row: (-row["postings"], row["skill"])),
        },
        "counted_postings": [
            {"posting_id": posting_id, "input_fingerprint": selected[posting_id]}
            for posting_id in sorted(records)
        ],
        "quality": {
            "eligible_postings": len(records) + len(exclusions),
            "excluded_postings": [
                {"posting_id": posting_id, "reason": exclusions[posting_id]}
                for posting_id in sorted(exclusions)
            ],
            "empty_extraction_postings": empty_count,
            "uncertain_only_postings": uncertain_only_count,
            "uncertain_skills": [
                {"skill": name, "postings": uncertain_counts[name]}
                for name in sorted(uncertain_counts)
            ],
        },
        "policy": {
            "selection_version": selection_version,
            "aggregation_version": AGGREGATION_VERSION,
            "count_confidence": "high",
        },
    }
