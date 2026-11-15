"""화면과 배치가 함께 사용하는 서비스 직무 코드와 이름."""
from __future__ import annotations

JOBS = {"cloud_engineer": "클라우드 엔지니어"}


def validate_role(job_category: str, role: str) -> None:
    """공고의 직무가 배치에서 지정한 서비스 직무와 같은지 확인한다."""
    if job_category not in JOBS:
        raise ValueError(f"지원하지 않는 서비스 직무 코드: {job_category}")
    if role != JOBS[job_category]:
        raise ValueError(f"공고 직무가 {job_category}의 직무 이름과 다릅니다.")
