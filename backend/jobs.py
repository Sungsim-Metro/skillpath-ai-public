"""서비스에서 지원하는 직무와 검토 공고의 분석 대상 직무."""
from __future__ import annotations

JOBS = {"cloud_engineer": "클라우드 엔지니어"}
ANALYSIS_JOBS = {**JOBS, "ai_engineer": "AI 엔지니어"}
REVIEWED_DOMAINS = {"cloud_engineer": "cloud", "ai_engineer": "ai"}


def validate_role(job_category: str, role: str) -> None:
    """공고의 직무가 배치에서 지정한 분석 직무와 같은지 확인한다."""
    if job_category not in ANALYSIS_JOBS:
        raise ValueError(f"지원하지 않는 분석 직무 코드: {job_category}")
    if role != ANALYSIS_JOBS[job_category]:
        raise ValueError(f"공고 직무가 {job_category}의 직무 이름과 다릅니다.")
