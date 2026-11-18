"""공고 수집과 저장 연결 확인. 임시 파일과 모의 API만 사용한다."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pipeline import rocketpunch, storage


class RocketpunchStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch.object(rocketpunch, "RAW_DIR", self.root).start()
        self.upload = patch.object(rocketpunch, "maybe_upload").start()
        self.upload.return_value = None
        self.api = patch.object(rocketpunch, "api_get").start()
        self.api.side_effect = AssertionError("Unexpected API call")
        self.params = {
            "jobCategories": ["dev"], "keyword": "infra", "page": 1,
            "pageSize": 10, "sort": "DATE_DESC",
        }
        self.cache = self.root / (
            "list-jobCategories=dev-keyword=infra-page=1-pageSize=10-"
            "sort=DATE_DESC.json"
        )
        self.item = {
            "jobId": 1, "jobCategory": "dev",
            "company": {"name": "Example Company"}, "title": "Cloud Engineer",
        }
        self.listing = {
            "items": [self.item], "totalItems": 12, "totalPages": 2,
        }
        self.detail = {
            **self.item, "description": "AWS operations", "startAt": None,
        }

    def save_cache(self, data: object) -> None:
        self.cache.write_text(json.dumps(data), encoding="utf-8")

    def test_legacy_list_cache_is_read_and_uploaded_without_api_call(self) -> None:
        self.save_cache([self.item])
        listing = rocketpunch.load_listing(self.params)
        self.assertEqual(listing["items"], [self.item])
        self.api.assert_not_called()
        self.upload.assert_called_once_with(self.cache)
        self.assertEqual(json.loads(self.cache.read_text()), [self.item])

    def test_full_response_cache_preserves_metadata_and_list_page_contract(self) -> None:
        self.save_cache(self.listing)
        self.assertEqual(rocketpunch.load_listing(self.params), self.listing)
        self.assertEqual(rocketpunch.list_page(self.params), [self.item])
        self.api.assert_not_called()
        self.assertEqual(self.upload.call_count, 2)

    def test_new_list_fetch_saves_entire_api_response(self) -> None:
        self.api.side_effect = None
        self.api.return_value = self.listing
        items = rocketpunch.list_page(self.params)
        self.assertEqual(items, [self.item])
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")), self.listing
        )
        self.api.assert_called_once_with("/jobs", self.params)
        self.upload.assert_called_once_with(self.cache)

    def test_search_uses_the_same_persistence_path(self) -> None:
        self.api.side_effect = None
        self.api.return_value = self.listing
        args = Namespace(
            size=10, page=1, sort="DATE_DESC", keyword="infra", codes=["dev"]
        )
        with redirect_stdout(io.StringIO()):
            rocketpunch.cmd_search(args)
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")), self.listing
        )
        self.upload.assert_called_once_with(self.cache)

    def test_detail_cache_is_uploaded_without_fetching_again(self) -> None:
        path = self.root / "rp-1.raw.json"
        path.write_text(json.dumps(self.detail), encoding="utf-8")
        self.assertEqual(rocketpunch.load_or_fetch("1"), self.detail)
        self.api.assert_not_called()
        self.upload.assert_called_once_with(path)

    def test_upload_failure_leaves_detail_cache_for_retry(self) -> None:
        self.api.side_effect = None
        self.api.return_value = self.detail
        self.upload.side_effect = RuntimeError("S3 unavailable")
        with self.assertRaises((RuntimeError, SystemExit)):
            rocketpunch.load_or_fetch("1")
        path = self.root / "rp-1.raw.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.detail)
        self.upload.side_effect = None
        self.assertEqual(rocketpunch.load_or_fetch("1"), self.detail)
        self.api.assert_called_once_with("/jobs/1")
        self.assertEqual(self.upload.call_count, 2)

    def test_upload_failure_leaves_listing_cache_for_retry(self) -> None:
        self.api.side_effect = None
        self.api.return_value = self.listing
        self.upload.side_effect = RuntimeError("S3 unavailable")
        with self.assertRaises((RuntimeError, SystemExit)):
            rocketpunch.load_listing(self.params)
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")), self.listing
        )
        self.upload.side_effect = None
        self.assertEqual(rocketpunch.list_page(self.params), [self.item])
        self.api.assert_called_once_with("/jobs", self.params)
        self.assertEqual(self.upload.call_count, 2)

    def test_filter_matches_are_local_review_candidates(self) -> None:
        with patch.object(rocketpunch, "load_or_fetch", return_value=self.detail):
            with redirect_stdout(io.StringIO()):
                self.assertTrue(rocketpunch.process("1", "Cloud Engineer", ["cloud"]))
        path = rocketpunch.posting_path(rocketpunch.to_posting(self.detail, "Cloud Engineer"))
        self.assertTrue(path.exists())
        self.upload.assert_not_called()
        with patch.object(rocketpunch, "load_or_fetch", return_value=self.detail):
            with redirect_stdout(io.StringIO()):
                self.assertFalse(
                    rocketpunch.process("1", "Cloud Engineer", ["kafka"])
                )
        self.assertFalse(path.exists())
        self.upload.assert_not_called()

    def test_saved_json_bytes_are_the_same_on_every_os(self) -> None:
        # S3 키는 파일 바이트의 해시다. 줄바꿈이 OS마다 다르면 같은 응답이 다른 키가 된다.
        self.api.side_effect = lambda path, params=None: (
            self.listing if path == "/jobs" else self.detail
        )
        rocketpunch.load_listing(self.params)
        rocketpunch.load_or_fetch("1")
        with redirect_stdout(io.StringIO()):
            rocketpunch.process("1", "Cloud Engineer", ["cloud"])
        posting = rocketpunch.to_posting(self.detail, "Cloud Engineer")
        saved = {
            self.cache: self.listing,
            self.root / "rp-1.raw.json": self.detail,
            rocketpunch.posting_path(posting): posting,
        }
        for path, data in saved.items():
            with self.subTest(path=path.name):
                expected = json.dumps(data, ensure_ascii=False, indent=2)
                self.assertEqual(path.read_bytes(), expected.encode("utf-8"))

    def test_upload_dry_run_classifies_only_posting_files_without_cloud_calls(self) -> None:
        self.save_cache(self.listing)
        for filename in ("rp-1.raw.json", "rp-1.posting.json", "unrelated.json"):
            (self.root / filename).write_text('{"id": 1}', encoding="utf-8")
        reviewed = self.root / "rocketpunch-2026-11-17" / "postings-reviewed"
        for domain in ("ai", "cloud"):
            folder = reviewed / domain
            folder.mkdir(parents=True)
            (folder / "rp-2.posting.json").write_text(
                json.dumps({"id": "rp-2", "role": domain}), encoding="utf-8"
            )
        args = Namespace(bucket="example-postings", prefix="test", dry_run=True)
        output = io.StringIO()
        with patch.object(rocketpunch, "target_from_env", return_value=None):
            with patch.object(storage, "_client") as client:
                with patch.object(
                    rocketpunch, "upload_json", wraps=storage.upload_json
                ) as upload:
                    with redirect_stdout(output):
                        rocketpunch.cmd_upload(args)
        self.assertEqual(upload.call_count, 4)
        self.assertTrue(all(call.kwargs["dry_run"] for call in upload.call_args_list))
        client.assert_not_called()
        self.api.assert_not_called()
        self.assertIn("test/raw/lists/", output.getvalue())
        self.assertIn("test/raw/details/", output.getvalue())
        self.assertIn("test/processed/postings/", output.getvalue())
        self.assertNotIn("test/processed/postings/rp-1/", output.getvalue())
        self.assertEqual(output.getvalue().count("test/processed/postings/rp-2/"), 2)
        self.assertNotIn("unrelated.json", output.getvalue())

    def test_write_failure_preserves_legacy_posting_until_retry_succeeds(self) -> None:
        legacy = self.root / "rp-1.posting.json"
        legacy.write_text('{"id":"rp-1","text":"previous"}', encoding="utf-8")
        with patch.object(rocketpunch, "load_or_fetch", return_value=self.detail):
            with patch.object(rocketpunch, "write_json", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    rocketpunch.process("1", "Cloud Engineer", ["cloud"])
            self.assertTrue(legacy.exists())
            with redirect_stdout(io.StringIO()):
                self.assertTrue(rocketpunch.process("1", "Cloud Engineer", ["cloud"]))
        self.assertFalse(legacy.exists())
        posting = rocketpunch.to_posting(self.detail, "Cloud Engineer")
        self.assertTrue(rocketpunch.posting_path(posting).exists())

    def test_cli_help_exposes_collection_and_storage_commands(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pipeline.rocketpunch", "--help"],
            cwd=rocketpunch.ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        self.assertIn("collect", result.stdout)
        self.assertIn("upload", result.stdout)
        self.assertIn("export-eval", result.stdout)


if __name__ == "__main__":
    unittest.main()
