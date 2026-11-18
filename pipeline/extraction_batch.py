"""선정된 공고를 S3에서 읽어 추출 결과와 완성된 수요 집계를 저장한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ai.bedrock import USAGE_KEYS, add_usage
from ai.extract import extraction_settings, extract_with_meta, input_fingerprint
from backend.demand import build_snapshot
from backend.dynamodb import DynamoDBStore
from backend.jobs import ANALYSIS_JOBS, REVIEWED_DOMAINS, validate_role
from pipeline import storage
from pipeline.reviewed_postings import RAW_DIR, reviewed_paths

DEFAULT_SELECTION_VERSION = "rocketpunch-reviewed-v1"


@dataclass(frozen=True)
class BatchInput:
    path: Path
    content: bytes
    posting: dict
    s3_key: str
    fingerprint: str


def validate_configuration(job_category: str) -> None:
    """공개 데이터 저장에 필요한 설정을 AWS 호출 전에 확인한다."""
    if os.getenv("BEDROCK_MOCK", "1") != "0":
        raise ValueError("DynamoDB 저장은 BEDROCK_MOCK=0에서만 실행할 수 있습니다.")
    if job_category not in ANALYSIS_JOBS:
        raise ValueError(f"지원하지 않는 직무 코드: {job_category}")
    required = (
        "BEDROCK_MODEL_ID", "ROCKETPUNCH_S3_BUCKET",
        "DYNAMODB_EXTRACTIONS_TABLE", "DYNAMODB_DEMAND_TABLE",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError(f"필수 환경 변수가 비어 있습니다: {', '.join(missing)}")


def parse_exclusions(values: list[str]) -> dict[str, str]:
    """반복된 --exclude rp-ID=사유 인자를 검사한다."""
    exclusions: dict[str, str] = {}
    for value in values:
        posting_id, separator, reason = value.partition("=")
        posting_id, reason = posting_id.strip(), reason.strip()
        if not separator or not re.fullmatch(r"rp-\d+", posting_id) or not reason:
            raise ValueError("제외 공고는 --exclude rp-ID=사유 형식으로 지정하세요.")
        if posting_id in exclusions and exclusions[posting_id] != reason:
            raise ValueError(f"제외 사유가 중복 지정되었습니다: {posting_id}")
        exclusions[posting_id] = reason
    return exclusions


def _normalized_terms(terms: Sequence[str] | None) -> tuple[str, ...] | None:
    if terms is None:
        return None
    normalized = tuple(sorted({term.strip().lower() for term in terms if term.strip()}))
    if not normalized:
        raise ValueError("선정 필터 단어를 하나 이상 지정하세요.")
    return normalized


def selection_version_for_terms(
    terms: Sequence[str] | None, override: str | None = None,
) -> str:
    """실제로 적용하는 선정 필터에 맞는 규칙 버전을 만든다."""
    normalized = _normalized_terms(terms)
    if override is not None:
        if not override.strip():
            raise ValueError("선정 규칙 버전은 빈 문자열일 수 없습니다.")
        return override.strip()
    if normalized is None:
        return DEFAULT_SELECTION_VERSION
    serialized = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return f"{DEFAULT_SELECTION_VERSION}-title-{digest}"


def _freeze_inputs(
    paths: list[Path], job_category: str, settings: dict,
    target: storage.S3Target, exclusions: dict[str, str], terms: tuple[str, ...] | None,
) -> tuple[list[BatchInput], list[dict], int]:
    """파일 목록과 내용을 고정하고 공고별 입력 오류를 기록한다."""
    from pipeline.rocketpunch import matched_terms

    by_id: dict[str, Path] = {}
    for path in sorted(set(paths)):
        category, posting_id = storage.classify_file(path)
        if category != "processed/postings":
            raise ValueError(f"전처리 공고 파일을 지정하세요: {path.name}")
        if posting_id in by_id:
            raise ValueError(f"같은 공고 ID의 입력이 둘 이상입니다: {posting_id}")
        by_id[posting_id] = path
    inputs, failures = [], []
    eligible_ids = set()
    for posting_id, path in by_id.items():
        try:
            content = path.read_bytes()
            posting = json.loads(content.decode("utf-8-sig"))
            if not isinstance(posting, dict) or posting.get("id") != posting_id:
                raise ValueError("파일 이름과 공고 ID가 일치해야 합니다.")
            if not isinstance(posting.get("text"), str):
                raise ValueError("공고 text는 문자열이어야 합니다.")
            if not isinstance(posting.get("title"), str):
                raise ValueError("공고 title은 문자열이어야 합니다.")
            if terms is not None and not matched_terms(posting, terms, fields=("title",)):
                continue
            eligible_ids.add(posting_id)
            if posting_id in exclusions:
                continue
            validate_role(job_category, posting.get("role"))
            inputs.append(BatchInput(
                path, content, posting,
                storage.object_key(path, content, target.prefix),
                input_fingerprint(posting, job_category, settings),
            ))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            failures.append({"posting_id": posting_id, "error_type": type(exc).__name__})
    unknown = exclusions.keys() - eligible_ids
    if unknown:
        raise ValueError(f"현재 선정 목록에 없는 제외 공고: {', '.join(sorted(unknown))}")
    return inputs, failures, len(inputs) + len(failures)


def _read_s3_posting(batch_input: BatchInput, target: storage.S3Target, s3) -> dict:
    """고정한 바이트를 보관하고 정확히 그 객체를 분석 입력으로 읽는다."""
    uploaded = storage.upload_json(
        batch_input.path, target, client=s3, content=batch_input.content,
    )
    if uploaded.key != batch_input.s3_key:
        raise ValueError("고정한 공고의 S3 키가 업로드 결과와 다릅니다.")
    response = s3.get_object(Bucket=target.bucket, Key=batch_input.s3_key)
    body = response["Body"]
    try:
        content = body.read()
    finally:
        body.close()
    if content != batch_input.content:
        raise ValueError("S3 객체 내용이 고정한 공고 입력과 다릅니다.")
    return json.loads(content.decode("utf-8-sig"))


def _extraction_item(
    posting: dict, batch_input: BatchInput, job_category: str,
    extraction: dict, analysis: dict,
) -> dict:
    if extraction.get("posting_id") != posting["id"] or not isinstance(
        extraction.get("skills"), list
    ):
        raise ValueError("추출 결과의 공고 ID 또는 skills 형식이 잘못되었습니다.")
    return {
        "job_category": job_category,
        "posting_id": posting["id"],
        "source": "rocketpunch",
        "posting_meta": {
            name: posting.get(name) for name in ("title", "company", "url", "posted_at")
        },
        "extraction": extraction,
        "posting_s3_key": batch_input.s3_key,
        "posting_text_sha256": hashlib.sha256(posting["text"].encode("utf-8")).hexdigest(),
        "analysis": analysis,
        "input_fingerprint": batch_input.fingerprint,
    }


def run_batch(
    paths: list[Path], *, job_category: str, collected: str,
    force: bool = False, exclusions: dict[str, str] | None = None,
    selection_version: str | None = None, terms: Sequence[str] | None = None,
    store=None, s3_client=None,
) -> dict:
    """완성 집계만 공개하고 실행 요약을 반환한다. 호출자가 요약 JSON을 한 번 출력한다.

    직무별 배치는 하나씩 실행한다. 실패한 공고가 있으면 이전 수요 스냅샷을 유지한다.
    """
    validate_configuration(job_category)
    if not collected.strip():
        raise ValueError("수집일 또는 수집 기간을 지정하세요.")
    terms = _normalized_terms(terms)
    selection_version = selection_version_for_terms(terms, selection_version)
    exclusions = dict(exclusions or {})
    parse_exclusions([f"{posting_id}={reason}" for posting_id, reason in exclusions.items()])
    target = storage.target_from_env()
    settings = extraction_settings()
    inputs, failures, target_count = _freeze_inputs(
        paths, job_category, settings, target, exclusions, terms,
    )
    summary = {
        "run_id": uuid4().hex,
        "job_category": job_category,
        "target": target_count,
        "success": 0,
        "reused": 0,
        "failed": len(failures),
        "excluded": len(exclusions),
        "failures": failures,
        "usage": {name: 0 for name in USAGE_KEYS},
        "published": False,
    }
    if not inputs:
        return summary
    try:
        database = store if store is not None else DynamoDBStore.from_env()
        s3 = s3_client if s3_client is not None else storage._client()
    except Exception as exc:
        summary["batch_error"] = type(exc).__name__
        return summary
    selected = {item.posting["id"]: item.fingerprint for item in inputs}
    for batch_input in inputs:
        posting_id = batch_input.posting["id"]
        try:
            posting = _read_s3_posting(batch_input, target, s3)
            existing = database.get_extraction(job_category, posting_id)
            if existing and existing.get("input_fingerprint") == batch_input.fingerprint and not force:
                item = _extraction_item(
                    posting, batch_input, job_category,
                    existing["extraction"], existing["analysis"],
                )
                if item != existing:
                    database.save_extraction(item)
                summary["reused"] += 1
            else:
                try:
                    extraction, meta = extract_with_meta(posting)
                except Exception as exc:
                    add_usage(summary["usage"], getattr(exc, "usage", {}) or {})
                    raise
                add_usage(summary["usage"], meta.usage)
                analysis = {
                    **settings,
                    "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "usage": meta.usage,
                }
                item = _extraction_item(
                    posting, batch_input, job_category, extraction, analysis,
                )
                database.save_extraction(item)
            summary["success"] += 1
        except Exception as exc:
            failures.append({"posting_id": posting_id, "error_type": type(exc).__name__})
            summary["failed"] += 1

    if failures:
        return summary
    try:
        items = database.list_extractions(job_category)
        snapshot = build_snapshot(
            items, selected=selected, job_category=job_category, collected=collected,
            exclusions=exclusions, selection_version=selection_version,
            run_id=summary["run_id"],
        )
        database.save_demand(snapshot)
        summary["published"] = True
    except Exception as exc:
        summary["publication_error"] = type(exc).__name__
    return summary


def main() -> int:
    from pipeline.rocketpunch import load_env_file

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", required=True, help="공고 수집일 또는 수집 기간")
    parser.add_argument("--job-category", default="cloud_engineer", choices=sorted(ANALYSIS_JOBS))
    parser.add_argument("--force", action="store_true", help="같은 입력도 다시 추출")
    parser.add_argument("--exclude", action="append", default=[], metavar="ID=REASON")
    parser.add_argument("--terms", nargs="+", help="검토된 공고를 추가로 제한할 제목 단어 (기본: 전체)")
    parser.add_argument("--selection-version", help="선정 규칙 버전. 기본값은 필터에서 계산")
    args = parser.parse_args()
    load_env_file()
    summary = run_batch(
        reviewed_paths(REVIEWED_DOMAINS[args.job_category], raw_dir=RAW_DIR),
        job_category=args.job_category,
        collected=args.collected, force=args.force,
        exclusions=parse_exclusions(args.exclude), selection_version=args.selection_version,
        terms=args.terms,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["published"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
