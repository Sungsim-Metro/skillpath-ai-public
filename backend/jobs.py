"""서비스에서 지원하는 직무와 검토 공고의 분석 대상 직무.

2026-11-19: AI 엔지니어를 다시 넣었다(11/16 "클라우드 하나" 결정을 바꿈). 직무별 데이터 파일은 job_file 로 고른다.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_JOB = "cloud_engineer"
JOBS = {"cloud_engineer": "클라우드 엔지니어", "ai_engineer": "AI 엔지니어"}
ANALYSIS_JOBS = dict(JOBS)
REVIEWED_DOMAINS = {"cloud_engineer": "cloud", "ai_engineer": "ai"}


def validate_role(job_category: str, role: str) -> None:
    """공고의 직무가 배치에서 지정한 분석 직무와 같은지 확인한다."""
    if job_category not in ANALYSIS_JOBS:
        raise ValueError(f"지원하지 않는 분석 직무 코드: {job_category}")
    if role != ANALYSIS_JOBS[job_category]:
        raise ValueError(f"공고 직무가 {job_category}의 직무 이름과 다릅니다.")


def job_file(name: str, job_category: str = DEFAULT_JOB) -> str:
    """직무별 데이터 파일 이름. 클라우드는 예전 이름 그대로, 다른 직무는 이름 뒤에 직무 코드를 붙인다.

    예: job_file("skill_areas.json", "ai_engineer") → "skill_areas_ai_engineer.json"
    """
    if job_category not in ANALYSIS_JOBS:
        raise ValueError(f"지원하지 않는 직무 코드: {job_category}")
    if job_category == DEFAULT_JOB:
        return name
    path = Path(name)
    return f"{path.stem}_{job_category}{path.suffix}"
