"""임시 공고와 모의 AWS로 재사용, 실패 보존, 완성 집계 공개를 확인한다."""
from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

from ai.bedrock import BedrockJSONError, ConverseResult
from pipeline import extraction_batch as batch


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.corrupt_reads = False

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = kwargs["Body"]

    def get_object(self, **kwargs):
        self.reads.append(kwargs["Key"])
        content = self.objects[kwargs["Key"]]
        if self.corrupt_reads:
            content = b'{}'
        return {"Body": io.BytesIO(content)}


class MemoryStore:
    def __init__(self):
        self.partitions = {}
        self.snapshot = {"previous": True}
        self.save_count = 0
        self.publish_count = 0
        self.reject_ids = set()
        self.reject_demand = False
        self.omit_ids = set()

    @property
    def extractions(self):
        return self.partitions.get(self.snapshot.get("extraction_partition", "cloud_engineer"), {})

    def get_partition_extraction(self, partition, posting_id):
        return copy.deepcopy(self.partitions.get(partition, {}).get(posting_id))

    def stage_extraction(self, partition, item):
        if item["posting_id"] in self.reject_ids:
            raise ClientError({"Error": {"Code": "ValidationException"}}, "PutItem")
        self.save_count += 1
        rows = self.partitions.setdefault(partition, {})
        if item["posting_id"] in rows:
            raise AssertionError("Tests should not overwrite immutable rows.")
        rows[item["posting_id"]] = copy.deepcopy(item)

    def snapshot_extractions(self, job_category, snapshot):
        return copy.deepcopy([
            item for posting_id, item in self.partitions.get(snapshot["extraction_partition"], {}).items()
            if posting_id not in self.omit_ids
        ])

    def get_demand_snapshot(self, job_category):
        return copy.deepcopy(self.snapshot)

    def publish_batch(self, item, expected_base):
        if self.snapshot.get("snapshot_id") != expected_base:
            raise ValueError("The published snapshot changed.")
        if self.reject_demand:
            raise ClientError({"Error": {"Code": "ValidationException"}}, "PutItem")
        self.publish_count += 1
        self.snapshot = copy.deepcopy(item)


class ExtractionBatchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        config = {
            "BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "test-model",
            "ROCKETPUNCH_S3_BUCKET": "test-bucket",
            "ROCKETPUNCH_S3_PREFIX": "test/rocketpunch",
            "DYNAMODB_EXTRACTIONS_TABLE": "test-extractions",
            "DYNAMODB_DEMAND_TABLE": "test-demand",
        }
        environment = patch.dict(os.environ, config, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        extractor = patch.object(batch, "extract_with_meta", side_effect=self.extract)
        self.extractor = extractor.start()
        self.addCleanup(extractor.stop)
        self.store = MemoryStore()
        self.s3 = MemoryS3()
        self.path = self.write_posting("rp-1")

    def write_posting(self, posting_id, *, text="AWS operations", **changes):
        posting = {
            "id": posting_id, "role": "클라우드 엔지니어", "text": text,
            "title": "Cloud Engineer", "company": "Example Company",
            "url": f"https://example.com/jobs/{posting_id}", "posted_at": "2026-11-15",
        }
        posting.update(changes)
        path = self.root / f"{posting_id}.posting.json"
        path.write_bytes(json.dumps(posting, ensure_ascii=False).encode("utf-8"))
        return path

    def extract(self, posting):
        skills = []
        if posting["text"]:
            skills.append({
                "raw": "AWS", "normalized": "AWS", "type": "required",
                "section": None,
                "evidence": posting["text"], "confidence": "high", "ncs_unit": None,
            })
        extraction = {"posting_id": posting["id"], "skills": skills}
        meta = ConverseResult({}, 1, {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7})
        return extraction, meta

    def run_batch(self, paths=None, **kwargs):
        return batch.run_batch(
            [self.path] if paths is None else paths,
            job_category="cloud_engineer", collected="2026-11-15",
            store=self.store, s3_client=self.s3, **kwargs,
        )

    def test_legacy_writer_cannot_overwrite_a_concurrent_serverless_publish(self):
        canonical = {"posting_id": "rp-1", "input_fingerprint": "old", "posting_meta": {"title": "Old"}}
        self.store.partitions["cloud_engineer"] = {"rp-1": copy.deepcopy(canonical)}
        self.store.snapshot = {"snapshot_id": "legacy"}
        def concurrent_publish(posting):
            self.store.snapshot = {"snapshot_id": "winner", "extraction_partition": "run#role#winner"}
            return self.extract(posting)

        self.extractor.side_effect = concurrent_publish
        result = self.run_batch()
        self.assertFalse(result["published"])
        self.assertEqual(self.store.snapshot["snapshot_id"], "winner")
        self.assertEqual(self.store.partitions["cloud_engineer"]["rp-1"], canonical)

    def test_same_input_is_reused_and_never_increments_counts(self):
        first = self.run_batch()
        second = self.run_batch()
        self.assertTrue(first["published"])
        self.assertTrue(second["published"])
        self.assertEqual((second["success"], second["reused"], second["failed"]), (1, 1, 0))
        self.assertEqual(second["usage"]["totalTokens"], 0)
        self.assertEqual(self.extractor.call_count, 1)
        self.assertEqual(self.store.save_count, 2)  # Both immutable snapshots retain their complete rows.
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 1)
        self.assertEqual(self.store.snapshot["demand"]["skills"][0]["postings"], 1)
        self.assertEqual(len(self.s3.reads), 2)
        item = self.store.extractions["rp-1"]
        self.assertEqual(item["posting_s3_key"], self.s3.reads[0])
        self.assertEqual(item["analysis"]["usage"]["totalTokens"], 7)
        self.assertNotIn("text", item)
        self.assertNotIn("text", item["posting_meta"])

    def test_changed_input_failure_preserves_previous_success_and_snapshot(self):
        self.run_batch()
        before = copy.deepcopy(self.store.extractions["rp-1"])
        snapshot = copy.deepcopy(self.store.snapshot)
        self.write_posting("rp-1", text="AWS changed requirements")
        self.extractor.side_effect = BedrockJSONError("test failure", {"totalTokens": 9})
        result = self.run_batch()
        self.assertFalse(result["published"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["usage"]["totalTokens"], 9)
        self.assertEqual(self.store.extractions["rp-1"], before)
        self.assertEqual(self.store.snapshot, snapshot)

    def test_forced_failure_does_not_publish_prior_same_fingerprint(self):
        self.run_batch()
        snapshot = copy.deepcopy(self.store.snapshot)
        self.extractor.side_effect = BedrockJSONError("test failure", {"totalTokens": 12})
        result = self.run_batch(force=True)
        self.assertFalse(result["published"])
        self.assertEqual(result["reused"], 0)
        self.assertEqual(result["usage"]["totalTokens"], 12)
        self.assertEqual(self.store.snapshot, snapshot)

    def test_partial_failure_saves_success_without_publishing_incomplete_demand(self):
        canonical = {"posting_id": "rp-1", "input_fingerprint": "old", "posting_meta": {"title": "Old"}}
        self.store.partitions["cloud_engineer"] = {"rp-1": copy.deepcopy(canonical)}
        self.store.snapshot = {"snapshot_id": "legacy"}
        second = self.write_posting("rp-2")
        def extract(posting):
            if posting["id"] == "rp-2":
                raise BedrockJSONError("test failure", {"totalTokens": 5})
            return self.extract(posting)
        self.extractor.side_effect = extract
        result = self.run_batch([self.path, second])
        self.assertEqual((result["target"], result["success"], result["failed"]), (2, 1, 1))
        self.assertFalse(result["published"])
        self.assertEqual(result["usage"]["totalTokens"], 12)
        self.assertEqual(self.store.extractions, {"rp-1": canonical})
        self.assertEqual(self.store.snapshot, {"snapshot_id": "legacy"})
        self.assertEqual(result["failures"], [{"posting_id": "rp-2", "error_type": "BedrockJSONError"}])
        self.extractor.side_effect = self.extract
        retried = self.run_batch([self.path, second])
        self.assertEqual(retried["reused"], 1)
        self.assertTrue(retried["published"])
        self.assertEqual(self.store.partitions["cloud_engineer"]["rp-1"], canonical)

    def test_storage_failure_preserves_previous_row_and_counts_used_tokens(self):
        self.run_batch()
        before = copy.deepcopy(self.store.extractions["rp-1"])
        self.store.reject_ids.add("rp-1")
        result = self.run_batch(force=True)
        self.assertFalse(result["published"])
        self.assertEqual(result["usage"]["totalTokens"], 7)
        self.assertEqual(self.store.extractions["rp-1"], before)
        self.assertEqual(result["failures"][0]["error_type"], "ClientError")

    def test_demand_write_failure_preserves_previous_snapshot(self):
        self.store.reject_demand = True
        result = self.run_batch()
        self.assertFalse(result["published"])
        self.assertEqual(result["publication_error"], "ClientError")
        self.assertEqual(result["success"], 1)
        self.assertEqual(self.store.snapshot, {"previous": True})

    def test_missing_query_result_blocks_publication(self):
        self.store.omit_ids.add("rp-1")
        result = self.run_batch()
        self.assertFalse(result["published"])
        self.assertEqual(result["publication_error"], "ValueError")
        self.assertEqual(self.store.snapshot, {"previous": True})

    def test_exclusion_removes_old_result_from_denominator_and_skills(self):
        second = self.write_posting("rp-2")
        self.run_batch([self.path, second])
        self.extractor.reset_mock()
        result = self.run_batch([self.path, second], exclusions={"rp-2": "manual review"})
        self.assertTrue(result["published"])
        self.assertEqual((result["target"], result["excluded"]), (1, 1))
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 1)
        self.assertEqual(self.store.snapshot["quality"]["eligible_postings"], 2)
        self.assertEqual(self.store.snapshot["quality"]["excluded_postings"], [
            {"posting_id": "rp-2", "reason": "manual review"},
        ])
        self.extractor.assert_not_called()
        self.assertNotIn("rp-2", self.store.extractions)
        self.assertTrue(any("rp-2" in rows for rows in self.store.partitions.values()))

    def test_metadata_only_change_reuses_model_result_and_updates_reference(self):
        self.run_batch()
        old_key = self.store.extractions["rp-1"]["posting_s3_key"]
        self.write_posting("rp-1", title="Updated Cloud Engineer")
        result = self.run_batch()
        self.assertTrue(result["published"])
        self.assertEqual(result["reused"], 1)
        self.assertEqual(self.extractor.call_count, 1)
        item = self.store.extractions["rp-1"]
        self.assertNotEqual(item["posting_s3_key"], old_key)
        self.assertEqual(item["posting_meta"]["title"], "Updated Cloud Engineer")

    def test_s3_mismatch_prevents_model_call_and_storage(self):
        self.s3.corrupt_reads = True
        result = self.run_batch()
        self.assertEqual(result["failed"], 1)
        self.assertFalse(result["published"])
        self.extractor.assert_not_called()
        self.assertEqual(self.store.extractions, {})

    def test_role_mismatch_is_reported_without_analyzing_or_saving(self):
        self.write_posting("rp-1", role="Other role")
        result = self.run_batch()
        self.assertFalse(result["published"])
        self.assertEqual((result["success"], result["failed"]), (0, 1))
        self.extractor.assert_not_called()
        self.assertEqual(self.s3.objects, {})
        self.assertEqual(self.store.extractions, {})

    def test_frozen_content_is_used_when_local_file_changes_after_start(self):
        original = batch.storage.upload_json
        captured = self.path.read_bytes()
        def upload(path, target, **kwargs):
            self.write_posting("rp-1", text="Changed after batch start")
            return original(path, target, **kwargs)
        with patch.object(batch.storage, "upload_json", side_effect=upload):
            result = self.run_batch()
        self.assertTrue(result["published"])
        self.assertIn(captured, self.s3.objects.values())
        self.assertEqual(self.extractor.call_args.args[0]["text"], "AWS operations")

    def test_empty_success_counts_in_denominator(self):
        self.write_posting("rp-1", text="")
        result = self.run_batch()
        self.assertTrue(result["published"])
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 1)
        self.assertEqual(self.store.snapshot["demand"]["skills"], [])
        self.assertEqual(self.store.snapshot["quality"]["empty_extraction_postings"], 1)

    def test_zero_targets_preserve_snapshot_without_creating_aws_clients(self):
        self.run_batch()
        snapshot = copy.deepcopy(self.store.snapshot)
        filtered = self.write_posting("rp-2", title="Backend Engineer")
        self.extractor.reset_mock()
        cases = (
            ("empty", [], {}),
            ("filtered", [filtered], {}),
            ("excluded", [self.path], {"rp-1": "manual review"}),
        )
        for name, paths, exclusions in cases:
            with self.subTest(case=name):
                with (
                    patch.object(
                        batch.DynamoDBStore, "from_env", return_value=self.store,
                    ) as create_store,
                    patch.object(
                        batch.storage, "_client", return_value=self.s3,
                    ) as create_s3,
                ):
                    result = batch.run_batch(
                        paths, job_category="cloud_engineer", collected="2026-11-15",
                        exclusions=exclusions, terms=["cloud"],
                    )
                self.assertFalse(result["published"])
                self.assertEqual(
                    (result["target"], result["success"], result["failed"]),
                    (0, 0, 0),
                )
                self.assertEqual(result["excluded"], len(exclusions))
                self.assertEqual(self.store.snapshot, snapshot)
                self.assertEqual(self.store.publish_count, 1)
                create_store.assert_not_called()
                create_s3.assert_not_called()
        self.extractor.assert_not_called()

    def test_mock_and_missing_configuration_stop_before_aws(self):
        with patch.object(batch.DynamoDBStore, "from_env") as create_store:
            with patch.object(batch.storage, "_client") as create_s3:
                with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
                    with self.assertRaises(ValueError):
                        batch.run_batch([self.path], job_category="cloud_engineer", collected="today")
                with patch.dict(os.environ, {"DYNAMODB_DEMAND_TABLE": ""}):
                    with self.assertRaises(ValueError):
                        self.run_batch()
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValueError):
                        self.run_batch()
                create_store.assert_not_called()
                create_s3.assert_not_called()
        self.extractor.assert_not_called()
        self.assertEqual(self.store.extractions, {})

    def test_exclusion_parser_and_unknown_id_reject_mistakes(self):
        self.assertEqual(batch.parse_exclusions(["rp-1=manual review"]), {"rp-1": "manual review"})
        for values in (["rp-1="], ["wrong=reason"], ["rp-1=a", "rp-1=b"]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    batch.parse_exclusions(values)
        with self.assertRaises(ValueError):
            self.run_batch(exclusions={"rp-2": "unknown"})
        self.assertEqual(self.store.extractions, {})

    def test_client_initialization_failure_returns_summary_without_writes(self):
        with patch.object(batch.DynamoDBStore, "from_env", side_effect=RuntimeError("test")):
            result = batch.run_batch(
                [self.path], job_category="cloud_engineer", collected="2026-11-15",
                s3_client=self.s3,
            )
        self.assertFalse(result["published"])
        self.assertEqual(result["batch_error"], "RuntimeError")
        self.assertEqual(result["success"], 0)
        with patch.object(batch.storage, "_client", side_effect=RuntimeError("test")):
            result = batch.run_batch(
                [self.path], job_category="cloud_engineer", collected="2026-11-15",
                store=self.store,
            )
        self.assertFalse(result["published"])
        self.assertEqual(result["batch_error"], "RuntimeError")
        self.assertEqual(self.store.extractions, {})
        self.extractor.assert_not_called()

    def test_dataset_posting_is_counted_with_its_own_source_and_version(self):
        dataset = self.write_posting("hf-wanted-7", text="Kubernetes operations")
        result = self.run_batch([self.path, dataset])
        self.assertTrue(result["published"])
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 2)
        self.assertEqual(self.store.snapshot["demand"]["sources"], ["rocketpunch", "wanted-dataset"])
        self.assertEqual(
            self.store.snapshot["policy"]["selection_version"], "rocketpunch-reviewed-v1+wanted-dataset-v1",
        )
        self.assertEqual(batch.parse_exclusions(["hf-wanted-7=manual review"]), {"hf-wanted-7": "manual review"})

    def test_reviewed_paths_adds_dataset_only_when_asked(self):
        from pipeline.reviewed_postings import reviewed_paths

        for folder in ("rocketpunch-2026-11-17/postings-reviewed/cloud", "rocketpunch-2026-11-17/postings-reviewed/ai"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "rocketpunch-2026-11-17/postings-reviewed/cloud/rp-1.posting.json").write_text("{}", encoding="utf-8")
        self.assertEqual(len(reviewed_paths("cloud", raw_dir=self.root)), 1)
        with self.assertRaises(FileNotFoundError):  # 데이터셋 폴더가 없으면 조용히 줄이지 않는다
            reviewed_paths("cloud", raw_dir=self.root, include_dataset=True)
        (self.root / "hf/postings-reviewed/cloud").mkdir(parents=True)
        (self.root / "hf/postings-reviewed/cloud/hf-wanted-7.posting.json").write_text("{}", encoding="utf-8")
        names = [path.name for path in reviewed_paths("cloud", raw_dir=self.root, include_dataset=True)]
        self.assertEqual(names, ["hf-wanted-7.posting.json", "rp-1.posting.json"])
        self.assertEqual(reviewed_paths("ai", raw_dir=self.root, include_dataset=True), [])  # AI 용 데이터셋 폴더는 없어도 된다

    def test_explicit_title_filter_narrows_full_reviewed_demand(self):
        second = self.write_posting(
            "rp-2", title="Platform Engineer", text="Kubernetes operations",
        )
        first = self.run_batch([self.path, second])
        self.assertTrue(first["published"])
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 2)
        self.assertEqual(self.store.snapshot["policy"]["selection_version"], "rocketpunch-reviewed-v1")
        self.extractor.reset_mock()
        result = self.run_batch([self.path, second], terms=["cloud"])
        self.assertTrue(result["published"])
        self.assertEqual(result["target"], 1)
        self.assertEqual(self.store.snapshot["demand"]["total_postings"], 1)
        self.assertEqual(
            self.store.snapshot["policy"]["selection_version"],
            batch.selection_version_for_terms(["cloud"]),
        )
        self.assertNotIn("rp-2", self.store.extractions)
        self.assertTrue(any("rp-2" in rows for rows in self.store.partitions.values()))
        self.assertTrue(second.exists())
        self.extractor.assert_not_called()
        with self.assertRaises(ValueError):
            self.run_batch([self.path, second], terms=["cloud"], exclusions={"rp-2": "outside current policy"})

    def test_cli_analyzes_only_the_selected_reviewed_directory(self):
        reviewed = self.root / "rocketpunch-2026-11-17" / "postings-reviewed"
        (self.root / "hf" / "postings-reviewed").mkdir(parents=True)  # 오픈 데이터셋 폴더(비어 있음)
        cases = [
            ("cloud", "cloud_engineer", "클라우드 엔지니어", "rp-2"),
            ("ai", "ai_engineer", "AI 엔지니어", "rp-3"),
        ]
        for domain, _, role, posting_id in cases:
            (reviewed / domain).mkdir(parents=True)
            path = self.write_posting(posting_id, role=role, title="Research Platform")
            path.rename(reviewed / domain / path.name)
        for _, job_category, role, posting_id in cases:
            with (
                self.subTest(job_category=job_category),
                patch.object(batch, "RAW_DIR", self.root),
                patch("pipeline.rocketpunch.load_env_file"),
                patch("sys.argv", ["batch", "--job-category", job_category, "--collected", "2026-11-17"]),
                patch.object(batch.DynamoDBStore, "from_env", return_value=self.store),
                patch.object(batch.storage, "_client", return_value=self.s3),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(batch.main(), 0)
                snapshot = self.store.snapshot
                self.assertEqual(snapshot["demand"]["job_title"], role)
                self.assertEqual([row["posting_id"] for row in snapshot["counted_postings"]], [posting_id])

    def test_selection_version_tracks_effective_filter(self):
        self.assertEqual(batch.selection_version_for_terms(None), "rocketpunch-reviewed-v1")
        custom = batch.selection_version_for_terms(["Docker", "Kubernetes"])
        self.assertTrue(custom.startswith("rocketpunch-reviewed-v1-title-"))
        self.assertEqual(custom, batch.selection_version_for_terms(["kubernetes", "docker", "docker"]))
        self.assertNotEqual(custom, batch.selection_version_for_terms(["Docker"]))
        self.assertEqual(batch.selection_version_for_terms(["aws"], "custom-v2"), "custom-v2")
        with self.assertRaises(ValueError):
            batch.selection_version_for_terms([])
        with self.assertRaises(ValueError):
            batch.selection_version_for_terms(["aws"], " ")


if __name__ == "__main__":
    unittest.main()
