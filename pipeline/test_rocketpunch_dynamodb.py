"""수집 명령이 전처리 후 DynamoDB 배치를 한 번 실행하는지 확인한다."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pipeline import rocketpunch, storage


class RocketpunchDynamoDBTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(patch.stopall)
        patch.object(rocketpunch, "RAW_DIR", self.root).start()
        (self.root / "hf" / "postings-reviewed").mkdir(parents=True)  # 오픈 데이터셋 폴더(비어 있음)
        patch.object(rocketpunch, "maybe_upload", return_value=None).start()
        patch.dict(os.environ, {
            "BEDROCK_MOCK": "0",
            "BEDROCK_MODEL_ID": "test-model",
            "USAGE_LOG": "off",  # 테스트의 가짜 호출이 운영 기록에 쌓이지 않게
            "ROCKETPUNCH_S3_BUCKET": "example-postings",
            "DYNAMODB_EXTRACTIONS_TABLE": "test-extractions",
            "DYNAMODB_DEMAND_TABLE": "test-demand",
        }).start()
        self.args = Namespace(
            ids=["1"], role="클라우드 엔지니어", terms=["cloud"],
            job_category="cloud_engineer", collected="2026-11-15",
            force=False, exclude=[], selection_version=None,
        )
        self.detail = {"jobId": 1, "title": "Cloud Engineer", "description": "AWS operations"}

    def reviewed_posting(self) -> Path:
        path = self.root / "rocketpunch-2026-11-17/postings-reviewed/cloud/rp-3.posting.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        posting = rocketpunch.to_posting({**self.detail, "jobId": 3, "title": "Platform Engineer"})
        rocketpunch.write_json(path, posting)
        return path

    def test_fetch_publishes_only_previously_reviewed_postings(self) -> None:
        existing = self.root / "rp-2.posting.json"
        rocketpunch.write_json(existing, {"id": "rp-2"})
        reviewed = self.reviewed_posting()

        def check_saved(paths, **options):
            self.assertEqual(paths, [reviewed])
            self.assertEqual(
                json.loads((self.root / "rp-1.raw.json").read_bytes()), self.detail
            )
            posting = json.loads(paths[0].read_bytes())
            self.assertEqual(posting["text"], "[포지션소개]\nAWS operations")
            self.assertEqual(options["collected"], "2026-11-15")
            return {"published": True}

        with patch.object(rocketpunch, "api_get", return_value=self.detail) as api:
            with patch("pipeline.extraction_batch.run_batch", side_effect=check_saved) as run:
                with redirect_stdout(io.StringIO()):
                    rocketpunch.cmd_fetch(self.args)
        api.assert_called_once_with("/jobs/1")
        run.assert_called_once()

    def test_collect_finishes_before_running_one_batch(self) -> None:
        self.reviewed_posting()
        self.args.keyword = "infra"
        self.args.codes = ["dev"]
        self.args.sort = "DATE_DESC"
        self.args.size = 10
        self.args.pages = 2
        self.args.n = 2
        listing = [{"jobId": 1, "title": "Cloud Engineer"}, {"jobId": 2, "title": "Cloud Engineer"}]
        with patch.object(rocketpunch, "list_page", return_value=listing):
            with patch.object(rocketpunch, "process", return_value=True) as process:
                with patch("pipeline.extraction_batch.run_batch", return_value={"published": True}) as run:
                    with redirect_stdout(io.StringIO()):
                        rocketpunch.cmd_collect(self.args)
                    self.assertEqual(process.call_count, 2)
                    run.assert_called_once()

    def test_fetch_reaches_both_tables_and_reuses_extraction_on_retry(self) -> None:
        from backend.dynamodb import DynamoDBStore
        from pipeline.test_extraction_batch import MemoryS3, MemoryStore

        store, s3 = MemoryStore(), MemoryS3()
        self.reviewed_posting()
        response = json.dumps({"skills": [{
            "raw": "AWS", "normalized": "AWS", "type": "required",
            "evidence": "AWS operations",
        }]})
        usage = {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7}
        output = io.StringIO()
        with (
            patch.object(rocketpunch, "api_get", return_value=self.detail) as api,
            patch.object(rocketpunch, "maybe_upload", side_effect=storage.maybe_upload),
            patch.object(storage, "_client", return_value=s3),
            patch.object(DynamoDBStore, "from_env", return_value=store),
            patch("ai.bedrock._call", return_value=(response, "end_turn", usage)) as model,
            redirect_stdout(output),
        ):
            rocketpunch.cmd_fetch(self.args)
            rocketpunch.cmd_fetch(self.args)

        api.assert_called_once_with("/jobs/1")
        model.assert_called_once()
        self.assertEqual(set(store.extractions), {"rp-3"})
        extraction = store.extractions["rp-3"]["extraction"]
        self.assertEqual(extraction["skills"][0]["confidence"], "high")
        demand = store.snapshot["demand"]
        self.assertEqual(demand["total_postings"], 1)
        self.assertEqual(demand["skills"][0]["postings"], 1)
        self.assertTrue(any("/raw/details/" in key for key in s3.objects))
        self.assertTrue(any("/processed/postings/" in key for key in s3.objects))
        summaries = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(len(summaries), 2)
        self.assertTrue(all(summary["published"] for summary in summaries))
        self.assertEqual(summaries[1]["reused"], 1)

    def test_mock_mode_fails_before_requesting_api_data(self) -> None:
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
            with patch.object(rocketpunch, "api_get") as api:
                with self.assertRaises(ValueError):
                    rocketpunch.cmd_fetch(self.args)
                api.assert_not_called()
        self.assertEqual([path.name for path in self.root.iterdir()], ["hf"])  # 준비 단계에서 만든 빈 데이터셋 폴더뿐

    def test_partial_configuration_fails_before_api_call(self) -> None:
        with patch.dict(os.environ, {"DYNAMODB_DEMAND_TABLE": ""}):
            with patch.object(rocketpunch, "api_get") as api:
                with self.assertRaises(ValueError):
                    rocketpunch.cmd_fetch(self.args)
                api.assert_not_called()

    def test_collected_is_required_before_api_call(self) -> None:
        self.args.collected = None
        with patch.object(rocketpunch, "api_get") as api:
            with self.assertRaises(ValueError):
                rocketpunch.cmd_fetch(self.args)
            api.assert_not_called()

    def test_unconfigured_tables_keep_existing_local_collection(self) -> None:
        with patch.dict(os.environ, {
            "DYNAMODB_EXTRACTIONS_TABLE": "", "DYNAMODB_DEMAND_TABLE": ""
        }):
            with patch.object(rocketpunch, "api_get", return_value=self.detail):
                with patch("pipeline.extraction_batch.run_batch") as run:
                    with redirect_stdout(io.StringIO()):
                        rocketpunch.cmd_fetch(self.args)
                    run.assert_not_called()
        self.assertTrue(rocketpunch.posting_path(rocketpunch.to_posting(self.detail)).exists())

    def test_unpublished_batch_sets_failure_exit_and_prints_summary_once(self) -> None:
        output = io.StringIO()
        self.reviewed_posting()
        with patch("pipeline.extraction_batch.run_batch", return_value={"published": False}):
            with redirect_stdout(output):
                with self.assertRaises(SystemExit) as error:
                    rocketpunch.persist_extractions({"collected": "2026-11-15", "job_category": "cloud_engineer"})
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(output.getvalue().strip(), '{"published": false}')

    def test_exclusions_force_and_selection_rule_are_forwarded(self) -> None:
        self.args.exclude = ["rp-1=manual exclusion"]
        self.args.force = True
        self.args.terms = ["kubernetes"]
        options = rocketpunch.batch_options(self.args)
        self.assertEqual(options["exclusions"], {"rp-1": "manual exclusion"})
        self.assertTrue(options["force"])
        self.assertNotEqual(options["selection_version"], "cloud-aws-v1")
        self.args.selection_version = "custom-v2"
        self.assertEqual(rocketpunch.batch_options(self.args)["selection_version"], "custom-v2")


class FrozenUploadTest(unittest.TestCase):
    def test_upload_uses_captured_bytes_even_after_file_changes(self) -> None:
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rp-1.posting.json"
            captured = b'{"id":"rp-1","text":"original"}'
            path.write_bytes(b'{"id":"rp-1","text":"changed"}')
            client = Mock()
            target = storage.S3Target("example-postings")
            result = storage.upload_json(path, target, client=client, content=captured)
            self.assertEqual(result.key, storage.object_key(path, captured, target.prefix))
            self.assertEqual(client.put_object.call_args.kwargs["Body"], captured)


if __name__ == "__main__":
    unittest.main()
