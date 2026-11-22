"""Freeze reviewed, content-addressed S3 inputs for a single job category."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from ai.extract import extraction_settings, input_fingerprint
from backend.dynamodb import DynamoDBStore, json_numbers
from backend.jobs import ANALYSIS_JOBS, validate_role
from pipeline import storage
from pipeline.extraction_batch import BatchInput

PREFIX = "skillpath/batch/manifests/"
HEX = r"[a-f0-9]{64}"
SOURCE_KEY = re.compile(rf".*/processed/postings/({storage.POSTING_ID})/({HEX})\.json")


def encode(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def manifest_key(content: bytes) -> str:
    return f"{PREFIX}{hashlib.sha256(content).hexdigest()}.json"


def read_bytes(s3, bucket: str, key: str) -> bytes:
    stream = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        return stream.read()
    finally:
        stream.close()


def source_id(key: str) -> str:
    match = SOURCE_KEY.fullmatch(key)
    if not match:
        raise ValueError("Expected a content-addressed processed posting key.")
    return match.group(1)


def load_posting(s3, target, key: str, job: str, settings: dict) -> BatchInput:
    posting_id = source_id(key)
    path = Path(f"{posting_id}.posting.json")
    content = read_bytes(s3, target.bucket, key)
    if storage.object_key(path, content, target.prefix) != key:
        raise ValueError(f"Posting object key or byte hash mismatch for {posting_id}.")
    posting = json.loads(content.decode("utf-8-sig"))
    if (posting["id"] != posting_id or not isinstance(posting["text"], str)
            or not isinstance(posting["title"], str)):
        raise ValueError(f"Invalid posting content for {posting_id}.")
    validate_role(job, posting["role"])
    return BatchInput(path, content, posting, key, input_fingerprint(posting, job, settings))


def validate_manifest(doc: dict) -> None:
    if (doc["version"] != 1 or doc["job_category"] not in ANALYSIS_JOBS
            or not doc["collected"].strip() or not doc["selection_version"].strip()
            or not isinstance(doc["settings"], dict) or not doc["postings"]):
        raise ValueError("Incomplete reviewed corpus manifest.")
    ids = set()
    for row in doc["postings"]:
        posting_id = source_id(row["s3_key"])
        if (row["posting_id"] != posting_id or posting_id in ids
                or not re.fullmatch(HEX, row["input_fingerprint"])):
            raise ValueError("Invalid or duplicate posting in manifest.")
        ids.add(posting_id)
    for posting_id, reason in doc["exclusions"].items():
        if (not re.fullmatch(storage.POSTING_ID, posting_id) or posting_id in ids
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("Invalid posting exclusion.")


def load_manifest(s3, target, key: str) -> dict:
    if not re.fullmatch(rf"{PREFIX}{HEX}\.json", key):
        raise ValueError("Invalid manifest key.")
    content = read_bytes(s3, target.bucket, key)
    if manifest_key(content) != key:
        raise ValueError("Manifest byte hash mismatch.")
    doc = json.loads(content)
    validate_manifest(doc)
    return doc


def store_manifest(s3, target, doc: dict) -> str:
    from botocore.exceptions import ClientError

    validate_manifest(doc)
    content = encode(doc)
    key = manifest_key(content)
    try:
        s3.put_object(Bucket=target.bucket, Key=key, Body=content,
                      ContentType="application/json", IfNoneMatch="*")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
            raise
        if read_bytes(s3, target.bucket, key) != content:
            raise ValueError("Existing manifest differs from its content address.") from exc
    return key


def freeze_manifest(job: str, *, current: dict | None, store, s3, target,
                    reviewed_keys: list[str] | None = None, collected: str | None = None,
                    selection_version: str | None = None, exclusions: dict | None = None) -> dict:
    """Use explicit reviewed keys, or reconstruct the published cohort by ID and fingerprint."""
    settings = extraction_settings()
    existing: dict[str, dict] = {}
    selected: dict[str, str] = {}
    if reviewed_keys is None:
        if not current:
            raise ValueError("No published snapshot for this role.")
        counted = current["counted_postings"]
        selected = {row["posting_id"]: row["input_fingerprint"] for row in counted}
        if len(selected) != current["demand"]["total_postings"] or len(selected) != len(counted):
            raise ValueError("Invalid counted_postings in the published snapshot.")
        existing = {r["posting_id"]: json_numbers(r) for r in store.snapshot_extractions(job, current)}
        reviewed_keys = [existing[pid]["posting_s3_key"] for pid in sorted(selected)]
        collected = current["demand"]["collected"]
        selection_version = current["policy"]["selection_version"]
        exclusions = {r["posting_id"]: r["reason"] for r in current["quality"]["excluded_postings"]}
        if selected:
            analysis = existing[next(iter(selected))]["analysis"]
            settings = {name: analysis[name] for name in settings}
    if not isinstance(reviewed_keys, list) or not collected or not selection_version:
        raise ValueError("Reviewed keys, collection period and selection version are required.")
    rows = []
    for key in reviewed_keys:
        source = load_posting(s3, target, key, job, settings)
        pid = source.posting["id"]
        if existing:
            if (source.fingerprint != selected[pid]
                    or source.fingerprint != existing[pid]["input_fingerprint"]
                    or hashlib.sha256(source.posting["text"].encode()).hexdigest()
                    != existing[pid]["posting_text_sha256"]):
                raise ValueError(f"Published extraction does not match the S3 input for {pid}.")
        rows.append({"posting_id": pid, "s3_key": key, "input_fingerprint": source.fingerprint})
    doc = {"version": 1, "job_category": job, "collected": collected,
           "selection_version": selection_version, "settings": settings,
           "exclusions": exclusions or {}, "postings": sorted(rows, key=lambda r: r["posting_id"])}
    validate_manifest(doc)
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-category", required=True, choices=sorted(ANALYSIS_JOBS))
    parser.add_argument("--reviewed-keys", type=Path,
                        help="JSON list of exact, already reviewed processed S3 keys")
    parser.add_argument("--collected")
    parser.add_argument("--selection-version")
    parser.add_argument("--exclusions", type=Path, help="JSON object mapping excluded IDs to reasons")
    parser.add_argument("--upload", action="store_true", help="Write the immutable manifest to S3")
    parser.add_argument("--output", type=Path, help="Also write the manifest bytes locally")
    args = parser.parse_args()
    if not args.reviewed_keys and (args.collected or args.selection_version or args.exclusions):
        parser.error("When reconstructing a snapshot, its original selection metadata is mandatory.")
    store = DynamoDBStore.from_env()
    current = store.get_demand_snapshot(args.job_category)
    target = storage.target_from_env()
    if target is None:
        parser.error("ROCKETPUNCH_S3_BUCKET is required.")
    s3 = storage._client()
    doc = freeze_manifest(
        args.job_category, current=current, store=store, s3=s3, target=target,
        reviewed_keys=json.loads(args.reviewed_keys.read_text()) if args.reviewed_keys else None,
        collected=args.collected, selection_version=args.selection_version,
        exclusions=json.loads(args.exclusions.read_text()) if args.exclusions else None,
    )
    content = encode(doc)
    if args.output:
        args.output.write_bytes(content)
    key = store_manifest(s3, target, doc) if args.upload else manifest_key(content)
    print(json.dumps({"manifest_key": key, "expected_base_snapshot": (current or {}).get("snapshot_id"),
                      "verify_only": args.reviewed_keys is None}))


if __name__ == "__main__":
    main()
