"""Exercise the same Step Functions event contracts without making AWS calls."""
import copy
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError

from ai.bedrock import BedrockJSONError
from backend.demand import build_snapshot
from backend.dynamodb import DynamoDBStore
from pipeline import batch_manifest as manifests
from pipeline import serverless_batch as batch
from pipeline import storage


class S3:
    def __init__(self):
        self.data = {}
        self.writes = 0

    def get_object(self, *, Key, Bucket):
        return {"Body": io.BytesIO(self.data[Key])}

    def put_object(self, *, Key, Body, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        if Key in self.data:
            raise ClientError({"Error": {"Code": "412"}}, "PutObject")
        self.data[Key] = Body
        self.writes += 1


class ConditionalFailure(Exception):
    pass


class Table:
    def __init__(self):
        self.values = {}
        self.writes = 0
        self.meta = SimpleNamespace(client=SimpleNamespace(exceptions=SimpleNamespace(
            ConditionalCheckFailedException=ConditionalFailure)))

    def get_item(self, *, Key, ConsistentRead):
        assert ConsistentRead
        key = Key["job_category"], Key.get("posting_id")
        return {"Item": copy.deepcopy(self.values[key])} if key in self.values else {}

    def query(self, *, ExpressionAttributeValues, **kwargs):
        partition = ExpressionAttributeValues[":category"]
        return {"Items": [copy.deepcopy(row) for (key, _), row in self.values.items() if key == partition]}

    def put_item(self, *, Item, ConditionExpression=None):
        key = Item["job_category"], Item.get("posting_id")
        if ConditionExpression is not None:
            expr = ConditionExpression.get_expression()
            if expr["operator"] == "attribute_not_exists":
                allowed = key not in self.values
            else:
                name, expected = expr["values"]
                allowed = self.values.get(key, {}).get(name.name) == expected
            if not allowed:
                raise ConditionalFailure()
        self.values[key] = copy.deepcopy(Item)
        self.writes += 1


class ServerlessBatchTest(unittest.TestCase):
    job = "cloud_engineer"

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MODEL_ID": "model", "BEDROCK_MOCK": "0", "ROCKETPUNCH_S3_BUCKET": "data",
            "DYNAMODB_EXTRACTIONS_TABLE": "extractions", "DYNAMODB_DEMAND_TABLE": "demand"}))
        self.s3 = S3()
        self.target = storage.S3Target("data")
        self.rows, self.demand = Table(), Table()
        self.db = DynamoDBStore(self.rows, self.demand)
        self.enterContext(patch.object(batch, "_resources", return_value=(self.db, self.s3, self.target)))
        self.model = self.enterContext(patch.object(
            batch, "extract_with_meta", side_effect=lambda posting: (
                {"posting_id": posting["id"], "skills": []}, SimpleNamespace(usage={}))))

    def source(self, ident="rp-1", **changes):
        doc = {"id": ident, "text": "An AWS job", "title": "Engineer", "role": "클라우드 엔지니어"} | changes
        content = json.dumps(doc).encode()
        key = storage.object_key(Path(ident + ".posting.json"), content, self.target.prefix)
        self.s3.data[key] = content
        return key

    def fresh(self, keys, base=None):
        doc = manifests.freeze_manifest(
            self.job, current=None, store=self.db, s3=self.s3, target=self.target,
            reviewed_keys=keys, collected="2026-11-20", selection_version="reviewed")
        key = manifests.store_manifest(self.s3, self.target, doc)
        return {"manifest_key": key, "expected_base_snapshot": base, "verify_only": False}

    def test_publish_verify_and_repeat_without_touching_canonical_rows(self):
        state = batch.prepare(self.fresh([self.source()]), None)
        self.assertEqual(state["chunks"], [0])
        self.assertEqual(batch.worker({**state, "chunk": 0}, None)["extracted"], 1)
        self.assertEqual(batch.worker({**state, "chunk": 0}, None)["reused"], 1)
        self.assertIsNone(self.db.get_demand_snapshot(self.job))
        self.assertIsNone(self.db.get_extraction(self.job, "rp-1"))
        self.assertTrue(batch.finalize(state, None)["published"])
        self.assertTrue(batch.finalize(state, None)["published"])
        current = self.db.get_demand_snapshot(self.job)
        self.assertEqual(self.db.snapshot_extractions(self.job, current)[0]["job_category"], self.job)
        self.assertEqual(self.db.get_extraction(self.job, "rp-1")["posting_id"], "rp-1")
        self.assertEqual(len(self.db.list_extractions(self.job)), 1)
        # Simulate a historical snapshot produced by the original CLI.
        record = self.db.snapshot_extractions(self.job, current)[0]
        self.db.save_extraction(record)
        original = build_snapshot(
            [record], selected={"rp-1": record["input_fingerprint"]}, job_category=self.job,
            collected="2026-11-20", selection_version="reviewed", exclusions={}, run_id="legacy")
        self.db.save_demand(original)
        self.db.save_extraction({**record, "posting_id": "rp-999"})
        doc = manifests.freeze_manifest(self.job, current=original, store=self.db, s3=self.s3, target=self.target)
        self.assertEqual([r["posting_id"] for r in doc["postings"]], ["rp-1"])
        verify = {"manifest_key": manifests.store_manifest(self.s3, self.target, doc),
                  "expected_base_snapshot": "legacy", "verify_only": True}
        writes = self.rows.writes, self.demand.writes, self.s3.writes
        verified = batch.prepare(verify, None)
        batch.worker({**verified, "chunk": 0}, None)
        self.assertEqual(batch.finalize(verified, None), {
            "job_category": self.job, "snapshot_id": "legacy", "total_postings": 1,
            "verified": True, "published": False})
        self.assertEqual(writes, (self.rows.writes, self.demand.writes, self.s3.writes))
        self.model.assert_called_once()
        original["demand"]["total_postings"] = 5
        self.db.save_demand(original)
        with self.assertRaisesRegex(ValueError, "differ"):
            batch.finalize(verified, None)

    def test_incomplete_or_stale_run_never_publishes(self):
        first = batch.prepare(self.fresh([self.source()]), None)
        other = batch.prepare(self.fresh([self.source(text="New")]), None)
        with self.assertRaisesRegex(ValueError, "Missing"):
            batch.finalize(first, None)
        batch.worker({**first, "chunk": 0}, None)
        batch.worker({**other, "chunk": 0}, None)
        winner = batch.finalize(other, None)
        with self.assertRaisesRegex(ValueError, "base"):
            batch.finalize(first, None)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.db.publish_batch({"job_category": self.job, "snapshot_id": "obsolete"}, None)
        self.assertEqual(self.db.get_demand_snapshot(self.job)["snapshot_id"], winner["snapshot_id"])

    def test_corrupt_source_duplicate_wrong_role_and_manifest_tampering(self):
        key = self.source()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.fresh([key, key])
        with self.assertRaises(ValueError):
            self.fresh([self.source(role="AI 엔지니어")])
        event = self.fresh([key])
        state = batch.prepare(event, None)
        self.s3.data[key] += b" "
        with self.assertRaisesRegex(ValueError, "byte hash"):
            batch.worker({**state, "chunk": 0}, None)
        self.s3.data[event["manifest_key"]] += b" "
        with self.assertRaisesRegex(ValueError, "Manifest byte hash"):
            batch.finalize(state, None)
        self.model.assert_not_called()

    def test_model_error_does_not_expose_raw_model_text_to_workflow_history(self):
        import traceback

        state = batch.prepare(self.fresh([self.source()]), None)
        self.model.side_effect = BedrockJSONError("SENSITIVE DATA FROM MODEL")
        try:
            batch.worker({**state, "chunk": 0}, None)
        except RuntimeError:
            trace = traceback.format_exc()
        else:
            self.fail("Extraction failure should stop this chunk.")
        self.assertIn("Extraction failed for rp-1 (BedrockJSONError)", trace)
        self.assertNotIn("SENSITIVE DATA FROM MODEL", trace)

    def test_changed_input_and_metadata_are_published_as_separate_versions(self):
        first = batch.prepare(self.fresh([self.source()]), None)
        batch.worker({**first, "chunk": 0}, None)
        old = batch.finalize(first, None)["snapshot_id"]
        current = self.db.get_demand_snapshot(self.job)
        other = batch.prepare(self.fresh([self.source(title="Corrected")], old), None)
        self.assertEqual(batch.worker({**other, "chunk": 0}, None)["reused"], 1)
        self.assertEqual(self.db.snapshot_extractions(self.job, current)[0]["posting_meta"]["title"], "Engineer")
        batch.finalize(other, None)
        latest = self.db.get_demand_snapshot(self.job)
        self.assertEqual(self.db.snapshot_extractions(self.job, latest)[0]["posting_meta"]["title"], "Corrected")
        self.model.assert_called_once()


if __name__ == "__main__":
    unittest.main()
