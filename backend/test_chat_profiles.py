"""닉네임별 프로필 저장과 명시적 변경의 원자성을 확인한다."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from boto3.dynamodb.conditions import Attr

from backend import chat_profiles


EMPTY = {"job": None, "skills": [], "experiences": [], "levels": {}}


class LocalProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.path = Path(work.name) / "app" / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "1", "SKILLPATH_MOCK_PROFILE_DB": str(self.path),
        }))
        self.resource = self.enterContext(patch.object(chat_profiles, "_resource"))

    def tearDown(self) -> None:
        self.resource.assert_not_called()

    def test_read_and_empty_update_do_not_create_a_store(self) -> None:
        self.assertEqual(chat_profiles.load_profile("new"), EMPTY)
        self.assertEqual(chat_profiles.update_profile("new"), EMPTY)
        self.assertFalse(self.path.parent.exists())

    def test_only_explicit_changes_persist_between_connections(self) -> None:
        first = chat_profiles.update_profile(
            "one", job="cloud_engineer", add_skills=["Docker", "Linux"],
            add_experiences=["API 배포", "Linux 운영"],
        )
        self.assertEqual(chat_profiles.load_profile("one"), first)
        self.assertEqual(chat_profiles.load_profile("two"), EMPTY)
        expected = {
            "job": "cloud_engineer", "skills": ["Linux", "Git"], "experiences": ["Linux 운영"], "levels": {},
        }
        for _ in range(2):
            result = chat_profiles.update_profile(
                "one", add_skills=["Git", "git"], remove_skills=["docker"],
                remove_experiences=["API 배포"],
            )
            self.assertEqual(result, expected)
        self.assertEqual(chat_profiles.load_profile("one"), expected)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_levels_merge_and_reject_unknown_values(self) -> None:
        chat_profiles.update_profile("one", levels={"C5-2": 3, "C1-2": 1})
        merged = chat_profiles.update_profile("one", levels={"C1-2": 4})
        self.assertEqual(merged["levels"], {"C5-2": 3, "C1-2": 4})
        self.assertEqual(chat_profiles.load_profile("one")["levels"], {"C5-2": 3, "C1-2": 4})
        for bad in ({"C5-2": 6}, {"X9-9": 1}, {"C5-2": "3"}):
            with self.assertRaises(ValueError):
                chat_profiles.update_profile("one", levels=bad)

    def test_parallel_updates_to_one_nickname_do_not_lose_changes(self) -> None:
        skills = ["Git", "Docker", "Linux", "AWS", "Python", "Terraform"]
        with ThreadPoolExecutor(max_workers=len(skills)) as workers:
            writes = [workers.submit(chat_profiles.update_profile, "one", add_skills=[s]) for s in skills]
            for write in writes:
                write.result()
        self.assertCountEqual(chat_profiles.load_profile("one")["skills"], skills)

    def test_default_path_uses_local_data_directory_and_private_file(self) -> None:
        with patch.dict(os.environ, {
            "SKILLPATH_MOCK_PROFILE_DB": "", "XDG_DATA_HOME": str(self.path.parent),
        }):
            chat_profiles.update_profile("one", add_skills=["Linux"])
            db = self.path.parent / "skillpath-ai" / "chat-profiles.sqlite3"
            self.assertTrue(db.is_file())
            if os.name == "posix":
                self.assertEqual(db.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_failed_change_preserves_the_previous_profile(self) -> None:
        saved = chat_profiles.update_profile("one", job="cloud_engineer", add_skills=["Git"])
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER reject BEFORE UPDATE ON profiles "
                       "BEGIN SELECT RAISE(ABORT, 'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            chat_profiles.update_profile("one", add_skills=["Docker"], remove_skills=["Git"])
        self.assertEqual(chat_profiles.load_profile("one"), saved)

    def test_unavailable_database_does_not_report_success(self) -> None:
        self.path.parent.mkdir()
        self.path.write_bytes(b"not a SQLite profile database")
        with self.assertRaises(sqlite3.DatabaseError):
            chat_profiles.load_profile("one")
        with self.assertRaises(sqlite3.DatabaseError):
            chat_profiles.update_profile("one", add_skills=["Git"])

    def test_invalid_input_does_not_write_partial_updates(self) -> None:
        with self.assertRaises(ValueError):
            chat_profiles.update_profile("  ", job="cloud_engineer")
        with self.assertRaises(ValueError):
            chat_profiles.update_profile("one", job="cloud_engineer", add_skills="Git")
        self.assertFalse(self.path.exists())


class DynamoProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "0", "DYNAMODB_PROFILE_TABLE": "mock-profiles",
        }))
        resource = self.enterContext(patch.object(chat_profiles, "_resource"))
        self.table = resource.return_value.Table.return_value
        self.conflict = type("ConditionalCheckFailedException", (Exception,), {})
        self.table.meta.client.exceptions.ConditionalCheckFailedException = self.conflict

    def test_missing_item_and_configuration_do_not_trigger_writes(self) -> None:
        self.table.get_item.return_value = {}
        self.assertEqual(chat_profiles.load_profile("one"), EMPTY)
        kwargs = self.table.get_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"nickname": "one"})
        self.assertIs(kwargs["ConsistentRead"], True)
        self.table.update_item.assert_not_called()
        with patch.dict(os.environ, {"DYNAMODB_PROFILE_TABLE": ""}):
            with self.assertRaises(ValueError):
                chat_profiles.update_profile("one", add_skills=["Docker"])

    def test_conflict_reapplies_only_requested_fields_to_the_latest_profile(self) -> None:
        concurrent = {"job": None, "skills": ["Git"], "experiences": ["API 배포"], "levels": {}}
        self.table.get_item.side_effect = [{}, {"Item": {"profile": concurrent}}]
        self.table.update_item.side_effect = [self.conflict(), {}]
        updated = chat_profiles.update_profile("one", add_skills=iter(["Docker"]))
        self.assertEqual(updated, {**concurrent, "skills": ["Git", "Docker"]})
        first, second = self.table.update_item.call_args_list
        self.assertEqual(first.kwargs["ConditionExpression"], Attr("profile").not_exists())
        self.assertEqual(second.kwargs["ConditionExpression"], Attr("profile").eq(concurrent))
        self.assertIn(updated, second.kwargs["ExpressionAttributeValues"].values())

    def test_duplicate_save_and_failed_write_are_distinguishable(self) -> None:
        current = {"job": None, "skills": ["Git"], "experiences": [], "levels": {}}
        self.table.get_item.return_value = {"Item": {"profile": current}}
        self.assertEqual(chat_profiles.update_profile("one", add_skills=["Git"]), current)
        self.table.update_item.assert_not_called()
        self.table.update_item.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            chat_profiles.update_profile("one", remove_skills=["Git"])
        self.table.get_item.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            chat_profiles.load_profile("one")


if __name__ == "__main__":
    unittest.main()
