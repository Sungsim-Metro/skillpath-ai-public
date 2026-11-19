"""채용공고 JSON을 종류별 S3 경로에 보관한다. 로컬 원본은 지우지 않는다.

전처리 공고는 로켓펀치(rp-번호)와 오픈 데이터셋의 원티드 공고(hf-wanted-번호, 2026-11-19 분석 입력에 포함) 두 가지다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PREFIX = "skillpath/rocketpunch"
DATASET_PREFIX = "skillpath/wanted-dataset"  # 오픈 데이터셋 공고의 S3 경로. 로켓펀치 경로 설정과 따로 둔다
DATASET_ID_PREFIX = "hf-wanted-"
POSTING_ID = r"(?:rp|hf-wanted)-\d+"


@dataclass(frozen=True)
class S3Target:
    bucket: str
    prefix: str = DEFAULT_PREFIX


@dataclass(frozen=True)
class UploadResult:
    key: str
    uploaded: bool
    dry_run: bool = False


def target_from_env() -> S3Target | None:
    """버킷이 비어 있으면 기존처럼 로컬에만 저장한다."""
    bucket = os.getenv("ROCKETPUNCH_S3_BUCKET", "").strip()
    if not bucket:
        return None
    prefix = os.getenv("ROCKETPUNCH_S3_PREFIX", "").strip(" /") or DEFAULT_PREFIX
    return S3Target(bucket, prefix)


def classify_file(path: Path) -> tuple[str, str]:
    """공고 목록, 상세 원본, 전처리 결과를 구분한다. 다른 파일은 올리지 않는다."""
    name = path.name
    if name.startswith("list-") and name.endswith(".json"):
        return "raw/lists", name.removesuffix(".json")
    if re.fullmatch(r"rp-\d+\.raw\.json", name):
        return "raw/details", name.removesuffix(".raw.json")
    posting = re.fullmatch(rf"({POSTING_ID})(?:_.+)?\.posting\.json", name)
    if posting:
        return "processed/postings", posting.group(1)
    raise ValueError(f"지원하지 않는 공고 파일 이름: {name}")


def source_of(posting_id: str) -> str:
    """공고 ID로 데이터 출처 이름을 정한다(수요 집계의 sources 에 들어간다)."""
    return "wanted-dataset" if posting_id.startswith(DATASET_ID_PREFIX) else "rocketpunch"


def object_key(path: Path, content: bytes, prefix: str) -> str:
    """내용이 같으면 같은 키, 내용이 바뀌면 새 키를 만들어 이전 원본을 보존한다."""
    category, identifier = classify_file(path)
    digest = hashlib.sha256(content).hexdigest()
    base = DATASET_PREFIX if identifier.startswith(DATASET_ID_PREFIX) else prefix.strip("/")
    parts = (base, category, identifier, f"{digest}.json")
    return "/".join(part for part in parts if part)


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "ap-northeast-2"))


def upload_json(
    path: Path,
    target: S3Target,
    *,
    client=None,
    dry_run: bool = False,
    content: bytes | None = None,
) -> UploadResult:
    """JSON 원본 바이트를 올린다. content가 있으면 배치 시작 시 고정한 바이트를 쓴다."""
    if content is None:
        content = path.read_bytes()
    json.loads(content.decode("utf-8-sig"))
    key = object_key(path, content, target.prefix)
    if dry_run:
        return UploadResult(key, False, True)

    from botocore.exceptions import ClientError

    s3 = client if client is not None else _client()
    try:
        s3.put_object(
            Bucket=target.bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
            Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
            return UploadResult(key, False)
        raise
    return UploadResult(key, True)


def maybe_upload(path: Path) -> UploadResult | None:
    """설정된 경우에만 S3에 동기화한다. 실패하면 호출자가 재시도할 수 있게 오류를 낸다."""
    target = target_from_env()
    return upload_json(path, target) if target is not None else None
