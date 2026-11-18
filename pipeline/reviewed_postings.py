"""RocketPunch 검토본에서 직무별 또는 전체 분석 입력을 찾는다."""
from __future__ import annotations

from pathlib import Path

from backend.jobs import REVIEWED_DOMAINS
from pipeline.storage import classify_file

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
SNAPSHOT = "rocketpunch-2026-11-17"
DOMAINS = tuple(sorted(REVIEWED_DOMAINS.values()))


def reviewed_paths(domain: str | None = None, *, raw_dir: Path = RAW_DIR) -> list[Path]:
    """직무별 공고를 반환한다. 전체 분석 시 두 직무에 속하는 ID는 한 번만 반환한다."""
    if domain is not None and domain not in DOMAINS:
        raise ValueError(f"지원하지 않는 검토 도메인: {domain}")
    by_id: dict[str, Path] = {}
    domains = DOMAINS if domain is None else (domain,)
    for name in domains:
        folder = raw_dir / SNAPSHOT / "postings-reviewed" / name
        if not folder.is_dir():
            raise FileNotFoundError(f"검토된 공고 폴더가 없습니다: {folder}")
        domain_ids: set[str] = set()
        for path in sorted(folder.glob("rp-*.posting.json")):
            _, posting_id = classify_file(path)
            if posting_id in domain_ids:
                raise ValueError(f"{folder}에 같은 공고 ID의 파일이 둘 이상입니다: {posting_id}")
            domain_ids.add(posting_id)
            by_id.setdefault(posting_id, path)
    return [by_id[posting_id] for posting_id in sorted(by_id)]
