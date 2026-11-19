"""검토된 공고에서 직무별 또는 전체 분석 입력을 찾는다.

로켓펀치 검토본이 기본이다. include_dataset=True 이면 오픈 데이터셋의 원티드 공고(data/raw/hf/postings-reviewed/)도 함께 준다
(2026-11-19 분석 입력에 포함, 이용 조건과 한계는 docs/data-sources.md).
"""
from __future__ import annotations

from pathlib import Path

from backend.jobs import REVIEWED_DOMAINS
from pipeline.storage import classify_file

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
SNAPSHOT = "rocketpunch-2026-11-17"
DATASET_SNAPSHOT = "hf"
DOMAINS = tuple(sorted(REVIEWED_DOMAINS.values()))


def reviewed_paths(
    domain: str | None = None, *, raw_dir: Path = RAW_DIR, include_dataset: bool = False,
) -> list[Path]:
    """직무별 공고를 반환한다. 전체 분석 시 두 직무에 속하는 ID는 한 번만 반환한다.

    include_dataset=True 인데 데이터셋 폴더가 없으면 오류를 낸다(수요 집계가 조용히 줄어들지 않게).
    데이터셋에 해당 직무 폴더가 없는 것은 정상이다(AI 엔지니어용 원티드 공고는 수집하지 않았다).
    """
    if domain is not None and domain not in DOMAINS:
        raise ValueError(f"지원하지 않는 검토 도메인: {domain}")
    by_id: dict[str, Path] = {}
    domains = DOMAINS if domain is None else (domain,)
    for name in domains:
        folder = raw_dir / SNAPSHOT / "postings-reviewed" / name
        if not folder.is_dir():
            raise FileNotFoundError(f"검토된 공고 폴더가 없습니다: {folder}")
        files = sorted(folder.glob("rp-*.posting.json"))
        if include_dataset:
            dataset = raw_dir / DATASET_SNAPSHOT / "postings-reviewed"
            if not dataset.is_dir():
                raise FileNotFoundError(f"오픈 데이터셋 공고 폴더가 없습니다: {dataset}")
            files += sorted((dataset / name).glob("hf-wanted-*.posting.json"))
        domain_ids: set[str] = set()
        for path in files:
            _, posting_id = classify_file(path)
            if posting_id in domain_ids:
                raise ValueError(f"{folder}에 같은 공고 ID의 파일이 둘 이상입니다: {posting_id}")
            domain_ids.add(posting_id)
            by_id.setdefault(posting_id, path)
    return [by_id[posting_id] for posting_id in sorted(by_id)]
