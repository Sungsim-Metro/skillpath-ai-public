"""Step Functions handlers for reviewed posting extraction and atomic publication."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ai.bedrock import BedrockJSONError
from ai.extract import extract_with_meta, extraction_settings
from backend.demand import build_snapshot
from backend.dynamodb import DynamoDBStore
from pipeline import storage
from pipeline.batch_manifest import encode, load_manifest, load_posting
from pipeline.extraction_batch import _extraction_item, validate_configuration

CHUNK_SIZE = 4
COMPARISON_FIELDS = ("job_category", "counted_postings", "quality", "policy", "demand")


def _resources():
    target = storage.target_from_env()
    if target is None:
        raise ValueError("ROCKETPUNCH_S3_BUCKET is required.")
    return DynamoDBStore.from_env(), storage._client(), target


def _request(event: dict, store, s3, target) -> tuple[dict, dict, dict]:
    manifest_key = event["manifest_key"]
    base = event["expected_base_snapshot"]
    verify = event["verify_only"]
    if type(verify) is not bool or (base is not None and (not isinstance(base, str) or not base)):
        raise ValueError("An explicit verify_only boolean and base snapshot ID or null are required.")
    manifest = load_manifest(s3, target, manifest_key)
    job = manifest["job_category"]
    validate_configuration(job)
    run_id = hashlib.sha256(encode({"manifest_key": manifest_key, "expected_base_snapshot": base})).hexdigest()
    if "run_id" in event and event["run_id"] != run_id:
        raise ValueError("Run ID differs from the selected manifest and base snapshot.")
    state = {"manifest_key": manifest_key, "expected_base_snapshot": base, "verify_only": verify,
             "job_category": job, "run_id": run_id}
    current = store.get_demand_snapshot(job) or {}
    if verify and not current:
        raise ValueError("Verification requires an existing published snapshot.")
    if current.get("snapshot_id") != base and (verify or current.get("snapshot_id") != run_id):
        raise ValueError("The expected base snapshot is no longer current.")
    return state, manifest, current


def prepare(event: dict, context) -> dict:
    """Return small chunk indices; posting texts and extraction payloads stay out of workflow history."""
    state, manifest, _ = _request(event, *_resources())
    count = len(manifest["postings"])
    state["chunks"] = list(range((count + CHUNK_SIZE - 1) // CHUNK_SIZE))
    return state


def _partition(state: dict) -> str:
    return f"run#{state['job_category']}#{state['run_id']}"


def _check_record(record: dict | None, entry: dict) -> None:
    if (not record or record["input_fingerprint"] != entry["input_fingerprint"]
            or record["posting_s3_key"] != entry["s3_key"]):
        raise ValueError(f"Missing or inconsistent extraction for {entry['posting_id']}.")


def worker(event: dict, context) -> dict:
    """Validate S3 inputs and complete a bounded chunk without changing the published view."""
    store, s3, target = _resources()
    state, manifest, current = _request(event, store, s3, target)
    index = event["chunk"]
    entries = manifest["postings"]
    if type(index) is not int or index < 0 or index * CHUNK_SIZE >= len(entries):
        raise ValueError("Invalid chunk index.")
    job = state["job_category"]
    partition = _partition(state)
    results = {"checked": 0, "reused": 0, "extracted": 0}
    for entry in entries[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]:
        source = load_posting(s3, target, entry["s3_key"], job, manifest["settings"])
        if source.fingerprint != entry["input_fingerprint"]:
            raise ValueError(f"Fingerprint mismatch for {entry['posting_id']}.")
        pid = entry["posting_id"]
        results["checked"] += 1
        if not state["verify_only"]:
            staged = store.get_partition_extraction(partition, pid)
            if staged:
                _check_record(staged, entry)
                results["reused"] += 1
                continue
        existing = store.get_partition_extraction(current.get("extraction_partition", job), pid)
        if state["verify_only"]:
            _check_record(existing, entry)
            expected_text_hash = hashlib.sha256(source.posting["text"].encode()).hexdigest()
            if existing["posting_text_sha256"] != expected_text_hash:
                raise ValueError(f"Stored posting text hash mismatch for {pid}.")
            results["reused"] += 1
            continue
        if existing and existing["input_fingerprint"] == source.fingerprint:
            extraction, analysis = existing["extraction"], existing["analysis"]
            results["reused"] += 1
        else:
            if manifest["settings"] != extraction_settings():
                raise ValueError("Deployed extraction settings do not match the manifest.")
            try:
                extraction, meta = extract_with_meta(source.posting)
            except (BedrockJSONError, ValueError, TypeError, KeyError) as exc:
                # Workflow history and Lambda logs must not contain model output.
                raise RuntimeError(f"Extraction failed for {pid} ({type(exc).__name__}).") from None
            analysis = {**manifest["settings"], "usage": meta.usage,
                        "extracted_at": datetime.now(timezone.utc).isoformat()}
            results["extracted"] += 1
        item = _extraction_item(source.posting, source, job, extraction, analysis)
        store.stage_extraction(partition, item)
    return results


def finalize(event: dict, context) -> dict:
    """Rebuild from the exact selected fingerprints, then verify or compare-and-swap."""
    store, s3, target = _resources()
    state, manifest, current = _request(event, store, s3, target)
    job = state["job_category"]
    partition = _partition(state)
    records = store.snapshot_extractions(
        job, current if state["verify_only"] else {"extraction_partition": partition})
    by_id = {row["posting_id"]: row for row in records}
    selected = {}
    for entry in manifest["postings"]:
        pid = entry["posting_id"]
        _check_record(by_id.get(pid), entry)
        selected[pid] = entry["input_fingerprint"]
    if not state["verify_only"] and set(by_id) != set(selected):
        raise ValueError("Staged partition does not match the selected posting IDs.")
    snapshot = build_snapshot(
        records, selected=selected, job_category=job, collected=manifest["collected"],
        exclusions=manifest["exclusions"], selection_version=manifest["selection_version"],
        run_id=state["run_id"],
    )
    if state["verify_only"]:
        if any(snapshot[field] != current[field] for field in COMPARISON_FIELDS):
            raise ValueError("Rebuilt demand, selected postings or policy differ from the published snapshot.")
        latest = store.get_demand_snapshot(job) or {}
        if latest.get("snapshot_id") != current["snapshot_id"]:
            raise ValueError("The published snapshot changed during verification.")
    else:
        snapshot["extraction_partition"] = partition
        snapshot["source_manifest"] = state["manifest_key"]
        store.publish_batch(snapshot, state["expected_base_snapshot"])
    return {"job_category": job, "snapshot_id": current["snapshot_id"] if state["verify_only"] else state["run_id"],
            "total_postings": len(selected), "published": not state["verify_only"],
            "verified": state["verify_only"]}
