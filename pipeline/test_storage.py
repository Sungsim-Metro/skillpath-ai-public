"""S3 분류, 중복 방지, 업로드 실패 처리 확인. 실제 AWS는 호출하지 않는다."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from botocore.loaders import create_loader
from botocore.model import ServiceModel
from botocore.validate import validate_parameters

from pipeline import storage


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = storage.S3Target("example-postings")
        self.content = b'{"jobId": 1}'
        self.path = self.root / "rp-1.raw.json"
        self.path.write_bytes(self.content)
        self.client = Mock()

    def test_each_data_type_has_a_distinct_prefix(self) -> None:
        cases = {
            "list-keyword=infra-page=1.json": (
                "raw/lists", "list-keyword=infra-page=1"
            ),
            "rp-1.raw.json": ("raw/details", "rp-1"),
            "rp-1.posting.json": ("processed/postings", "rp-1"),
            "rp-1_회사_Cloud-Engineer.posting.json": ("processed/postings", "rp-1"),
            "hf-wanted-162049.posting.json": ("processed/postings", "hf-wanted-162049"),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(storage.classify_file(Path(filename)), expected)
        with self.assertRaises(ValueError):
            storage.classify_file(Path("unrelated.json"))

    def test_dataset_posting_goes_to_its_own_prefix_and_source(self) -> None:
        path = self.root / "hf-wanted-7.posting.json"
        digest = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(
            storage.object_key(path, self.content, "custom/rocketpunch"),
            f"skillpath/wanted-dataset/processed/postings/hf-wanted-7/{digest}.json",
        )
        self.assertEqual(storage.source_of("hf-wanted-7"), "wanted-dataset")
        self.assertEqual(storage.source_of("rp-7"), "rocketpunch")

    def test_readable_posting_name_keeps_the_same_identity_and_content_key(self) -> None:
        legacy = self.root / "rp-1.posting.json"
        readable = self.root / "rp-1_회사_Cloud-Engineer.posting.json"
        self.assertEqual(
            storage.object_key(legacy, self.content, self.target.prefix),
            storage.object_key(readable, self.content, self.target.prefix),
        )

    def test_unchanged_content_reuses_key_and_changes_get_new_keys(self) -> None:
        digest = hashlib.sha256(self.content).hexdigest()
        key = storage.object_key(self.path, self.content, "skillpath/rocketpunch")
        self.assertEqual(
            key, f"skillpath/rocketpunch/raw/details/rp-1/{digest}.json"
        )
        self.assertEqual(
            key,
            storage.object_key(self.path, self.content, "skillpath/rocketpunch"),
        )
        self.assertNotEqual(
            key,
            storage.object_key(
                self.path, b'{"jobId": 1, "title": "changed"}',
                "skillpath/rocketpunch",
            ),
        )

    def test_upload_preserves_bytes_and_disallows_overwriting(self) -> None:
        result = storage.upload_json(self.path, self.target, client=self.client)
        self.assertTrue(result.uploaded)
        kwargs = self.client.put_object.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], self.target.bucket)
        self.assertEqual(kwargs["Key"], result.key)
        self.assertEqual(kwargs["Body"], self.content)
        self.assertEqual(kwargs["ContentType"], "application/json")
        self.assertEqual(kwargs["IfNoneMatch"], "*")
        self.assertEqual(
            kwargs["Metadata"]["sha256"], hashlib.sha256(self.content).hexdigest()
        )
        service = ServiceModel(
            create_loader().load_service_model("s3", "service-2"), service_name="s3"
        )
        validate_parameters(kwargs, service.operation_model("PutObject").input_shape)

    def test_existing_snapshot_is_skipped(self) -> None:
        self.client.put_object.side_effect = ClientError(
            {"Error": {"Code": "PreconditionFailed"},
             "ResponseMetadata": {"HTTPStatusCode": 412}},
            "PutObject",
        )
        result = storage.upload_json(self.path, self.target, client=self.client)
        self.assertFalse(result.uploaded)
        self.assertFalse(result.dry_run)
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_permission_error_is_reported_without_losing_local_file(self) -> None:
        self.client.put_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"},
             "ResponseMetadata": {"HTTPStatusCode": 403}},
            "PutObject",
        )
        with self.assertRaises(ClientError):
            storage.upload_json(self.path, self.target, client=self.client)
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_dry_run_does_not_contact_s3(self) -> None:
        result = storage.upload_json(
            self.path, self.target, client=self.client, dry_run=True
        )
        self.assertTrue(result.dry_run)
        self.assertFalse(result.uploaded)
        self.assertEqual(self.client.mock_calls, [])

    def test_invalid_json_is_rejected_before_contacting_s3(self) -> None:
        self.path.write_bytes(b'{"jobId":')
        with self.assertRaises(ValueError):
            storage.upload_json(self.path, self.target, client=self.client)
        self.assertEqual(self.client.mock_calls, [])

    def test_unconfigured_upload_does_not_create_a_client(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(storage, "upload_json") as upload:
                self.assertIsNone(storage.target_from_env())
                self.assertIsNone(storage.maybe_upload(self.path))
                upload.assert_not_called()

    def test_target_configuration_is_read_from_environment(self) -> None:
        config = {
            "ROCKETPUNCH_S3_BUCKET": "example-postings",
            "ROCKETPUNCH_S3_PREFIX": "test/rocketpunch",
        }
        with patch.dict(os.environ, config, clear=True):
            target = storage.target_from_env()
        self.assertEqual(target.bucket, config["ROCKETPUNCH_S3_BUCKET"])
        self.assertEqual(target.prefix, config["ROCKETPUNCH_S3_PREFIX"])


if __name__ == "__main__":
    unittest.main()
