"""닉네임별 프로필 저장과 명시적 변경의 원자성을 확인한다."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from boto3.dynamodb.conditions import Attr

from backend import chat_profiles
from backend.levels import level_guide


PASSWORD = "test-only passphrase!"
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
        for operation in (chat_profiles.load_profile, chat_profiles.update_profile):
            with self.assertRaises(ValueError):
                operation("new")
        with self.assertRaises(ValueError):
            chat_profiles.sign_in("new", PASSWORD)
        self.assertFalse(self.path.parent.exists())

    def test_only_explicit_changes_persist_between_connections(self) -> None:
        session = chat_profiles.create_profile("one", PASSWORD)
        first = chat_profiles.update_profile(
            session, job="cloud_engineer", add_skills=["Docker", "Linux"],
            add_experiences=["API 배포", "Linux 운영"],
        )
        self.assertEqual(chat_profiles.load_profile(session), first)
        self.assertEqual(chat_profiles.load_profile(chat_profiles.create_profile("two", PASSWORD)), EMPTY)
        expected = {
            "job": "cloud_engineer", "skills": ["Linux", "Git"], "experiences": ["Linux 운영"], "levels": {},
        }
        for _ in range(2):
            result = chat_profiles.update_profile(
                session, add_skills=["Git", "git"], remove_skills=["docker"],
                remove_experiences=["API 배포"],
            )
            self.assertEqual(result, expected)
        self.assertEqual(chat_profiles.load_profile(session), expected)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_levels_merge_and_reject_unknown_values(self) -> None:
        session = chat_profiles.create_profile("one", PASSWORD)
        chat_profiles.update_profile(session, levels={"C5-2": 3, "C1-2": 1})
        merged = chat_profiles.update_profile(session, levels={"C1-2": 4})
        self.assertEqual(merged["levels"], {"C5-2": 3, "C1-2": 4})
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {"C5-2": 3, "C1-2": 4})
        for bad in ({"C5-2": 6}, {"X9-9": 1}, {"C5-2": "3"}):
            with self.assertRaises(ValueError):
                chat_profiles.update_profile(session, levels=bad)

    def test_custom_levels_merge_into_one_named_skill_across_sessions(self) -> None:
        session = chat_profiles.create_profile("custom", PASSWORD)
        chat_profiles.update_profile(session, levels={"custom:Docker": 0, "C5-2": 3})
        reopened = chat_profiles.sign_in("custom", PASSWORD)
        updated = chat_profiles.update_profile(reopened, levels={"custom:docker": 2})
        self.assertEqual(updated["levels"], {"custom:Docker": 2, "C5-2": 3})
        self.assertEqual(chat_profiles.load_profile(session)["levels"], updated["levels"])

    def test_removing_then_restoring_a_question_keeps_its_answer(self) -> None:
        session = chat_profiles.create_profile("retired", PASSWORD)
        chat_profiles.update_profile(session, levels={"A1-1": 4, "C5-2": 2, "custom:Docker": 1})
        questions = level_guide()["subskills"]
        with patch.dict(questions):
            del questions["A1-1"]
            reopened = chat_profiles.sign_in("retired", PASSWORD)
            visible = {"C5-2": 2, "custom:Docker": 1}
            self.assertEqual(chat_profiles.load_profile(reopened)["levels"], visible)
            chat_profiles.update_profile(reopened, add_experiences=["example"])
            with self.assertRaises(ValueError):
                chat_profiles.update_profile(reopened, levels={"A1-1": 0})
            visible["custom:Docker"] = 0
            updated = chat_profiles.update_profile(reopened, levels={"custom:Docker": 0})
            self.assertEqual(updated["levels"], visible)
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {**visible, "A1-1": 4})
        self.assertEqual(chat_profiles.load_profile(session)["experiences"], ["example"])

    def test_invalid_stored_answers_still_reject_reads_and_edits(self) -> None:
        session = chat_profiles.create_profile("bad", PASSWORD)
        for key, value in (("Z99-2", True), ("Z99-2", 6), ("Z99-2", "2"),
                           ("C1-1", 1.5), ("Z-2", 3), ("custom:", 2)):
            with self.subTest(key=key, value=value):
                payload = json.dumps({"levels": {key: value}})
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE profiles SET payload = ? WHERE nickname = 'bad'", (payload,))
                with self.assertRaises(ValueError):
                    chat_profiles.load_profile(session)
                with self.assertRaises(ValueError):
                    chat_profiles.update_profile(session, add_skills=["Git"])
                with sqlite3.connect(self.path) as db:
                    self.assertEqual(db.execute("SELECT payload FROM profiles").fetchone()[0], payload)

    def test_parallel_updates_to_one_nickname_do_not_lose_changes(self) -> None:
        session = chat_profiles.create_profile("one", PASSWORD)
        skills = ["Git", "Docker", "Linux", "AWS", "Python", "Terraform"]
        with ThreadPoolExecutor(max_workers=len(skills)) as workers:
            writes = [workers.submit(chat_profiles.update_profile, session, add_skills=[s]) for s in skills]
            for write in writes:
                write.result()
        self.assertCountEqual(chat_profiles.load_profile(session)["skills"], skills)

    def test_default_path_uses_local_data_directory_and_private_file(self) -> None:
        with patch.dict(os.environ, {
            "SKILLPATH_MOCK_PROFILE_DB": "", "XDG_DATA_HOME": str(self.path.parent),
        }):
            session = chat_profiles.create_profile("one", PASSWORD)
            chat_profiles.update_profile(session, add_skills=["Linux"])
            db = self.path.parent / "skillpath-ai" / "chat-profiles.sqlite3"
            self.assertTrue(db.is_file())
            if os.name == "posix":
                self.assertEqual(db.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_failed_change_preserves_the_previous_profile(self) -> None:
        session = chat_profiles.create_profile("one", PASSWORD)
        saved = chat_profiles.update_profile(session, job="cloud_engineer", add_skills=["Git"])
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER reject BEFORE UPDATE ON profiles "
                       "BEGIN SELECT RAISE(ABORT, 'unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            chat_profiles.update_profile(session, add_skills=["Docker"], remove_skills=["Git"])
        self.assertEqual(chat_profiles.load_profile(session), saved)

    def test_unavailable_database_does_not_report_success(self) -> None:
        session = chat_profiles.create_profile("one", PASSWORD)
        self.path.write_bytes(b"not a SQLite profile database")
        with self.assertRaises(sqlite3.DatabaseError):
            chat_profiles.load_profile(session)
        with self.assertRaises(sqlite3.DatabaseError):
            chat_profiles.update_profile(session, add_skills=["Git"])

    def test_invalid_input_cannot_create_or_modify_the_store(self) -> None:
        with self.assertRaises(ValueError):
            chat_profiles.update_profile("  ", job="cloud_engineer")
        with self.assertRaises(ValueError):
            chat_profiles.create_profile("one", "")
        self.assertFalse(self.path.exists())
        session = chat_profiles.create_profile("one", PASSWORD)
        with self.assertRaises(ValueError):
            chat_profiles.update_profile(session, job="cloud_engineer", add_skills="Git")
        self.assertEqual(chat_profiles.load_profile(session), EMPTY)

    def test_sign_in_reopens_only_the_registered_profile(self) -> None:
        owner = chat_profiles.create_profile("  one  ", PASSWORD)
        chat_profiles.create_profile("two", PASSWORD)
        chat_profiles.update_profile(owner, add_experiences=["example"])
        reopened = chat_profiles.sign_in("one", PASSWORD)
        self.assertEqual(reopened.nickname, "one")
        self.assertEqual(chat_profiles.load_profile(reopened)["experiences"], ["example"])
        for nickname in ("one", "missing"):
            with self.assertRaises(chat_profiles.ProfileAuthError) as denied:
                chat_profiles.sign_in(nickname, "incorrect password")
            self.assertNotIn(nickname, str(denied.exception))
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.load_profile(replace(owner, nickname="two"))
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.update_profile(replace(owner, nickname="two"), add_skills=["Git"])
        self.assertNotIn(PASSWORD, repr(owner))

    def test_same_password_has_unique_salt_and_no_plaintext_on_disk(self) -> None:
        chat_profiles.create_profile("one", PASSWORD)
        chat_profiles.create_profile("two", PASSWORD)
        with sqlite3.connect(self.path) as db:
            credentials = [row[0] for row in db.execute("SELECT credential FROM profiles")]
        self.assertEqual(len(set(credentials)), 2)
        self.assertNotIn(PASSWORD.encode(), self.path.read_bytes())
        with self.assertRaises(ValueError):
            chat_profiles.create_profile("one", "another test password")
        with self.assertRaises(ValueError):
            chat_profiles.sign_in("one", "another test password")

    def test_nonempty_passwords_are_accepted_and_compared_exactly(self) -> None:
        cases = (("a", "A"), ("  ", " "), ("  한 ", "한"), ("한" * 342, "한" * 341),
                 ("z" * 1025, "z" * 1024))
        for i, (secret, different) in enumerate(cases):
            nickname = f"example-{i}"
            with self.subTest(nickname=nickname):
                session = chat_profiles.create_profile(nickname, secret)
                self.assertEqual(chat_profiles.sign_in(nickname, secret).nickname, session.nickname)
                with self.assertRaises(chat_profiles.ProfileAuthError):
                    chat_profiles.sign_in(nickname, different)
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.sign_in("example-0", "")

    def test_legacy_profile_cannot_be_claimed_or_erased(self) -> None:
        self.path.parent.mkdir()
        saved = json.dumps({**EMPTY, "skills": ["Linux"]})
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE profiles (nickname TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT INTO profiles VALUES (?, ?)", ("legacy", saved))
        for before_new_registration in (True, False):
            if not before_new_registration:
                chat_profiles.create_profile("new", PASSWORD)
            with self.assertRaises(ValueError):
                chat_profiles.sign_in("legacy", PASSWORD)
            with self.assertRaises(ValueError):
                chat_profiles.create_profile("legacy", PASSWORD)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM profiles WHERE nickname='legacy'").fetchone()
            self.assertEqual(row[0], saved)

    def test_operator_can_assign_only_an_existing_unclaimed_legacy_profile(self) -> None:
        self.path.parent.mkdir()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE profiles (nickname TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT INTO profiles VALUES (?, ?)", ("legacy", json.dumps({"skills": ["Linux"]})))
        with self.assertRaises(ValueError):
            chat_profiles.set_legacy_password("missing", PASSWORD)
        if os.name == "posix":
            self.path.chmod(0o644)
        self.assertIsNone(chat_profiles.set_legacy_password("legacy", PASSWORD))
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            chat_profiles.set_legacy_password("legacy", "a second test password")
        session = chat_profiles.sign_in("legacy", PASSWORD)
        self.assertEqual(chat_profiles.load_profile(session)["skills"], ["Linux"])

    def test_parallel_registration_has_one_owner(self) -> None:
        passwords = ["first test passphrase", "second test passphrase"]
        with ThreadPoolExecutor(max_workers=2) as workers:
            attempts = [workers.submit(chat_profiles.create_profile, "one", secret) for secret in passwords]
        winners = [secret for secret, attempt in zip(passwords, attempts) if attempt.exception() is None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(chat_profiles.sign_in("one", winners[0]).nickname, "one")
        with self.assertRaises(ValueError):
            chat_profiles.sign_in("one", next(secret for secret in passwords if secret not in winners))


class DynamoProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {
            "BEDROCK_MOCK": "0", "DYNAMODB_PROFILE_TABLE": "mock-profiles",
        }))
        resource = self.enterContext(patch.object(chat_profiles, "_resource"))
        self.table = resource.return_value.Table.return_value
        self.conflict = type("ConditionalCheckFailedException", (Exception,), {})
        self.table.meta.client.exceptions.ConditionalCheckFailedException = self.conflict

    def register(self):
        session = chat_profiles.create_profile("one", PASSWORD)
        registration = self.table.update_item.call_args.kwargs
        self.assertEqual(registration["ConditionExpression"], Attr("nickname").not_exists())
        self.credential = registration["ExpressionAttributeValues"][":credential"]
        self.table.update_item.reset_mock()
        return session

    def test_missing_and_legacy_records_do_not_allow_sign_in_or_overwrite(self) -> None:
        for item in ({}, {"Item": {"profile": {"skills": ["Git"]}}}):
            self.table.get_item.return_value = item
            with self.assertRaises(chat_profiles.ProfileAuthError):
                chat_profiles.sign_in("one", PASSWORD)
        request = self.table.get_item.call_args.kwargs
        self.assertEqual(request["Key"], {"nickname": "one"})
        self.assertIs(request["ConsistentRead"], True)
        self.table.update_item.assert_not_called()
        self.table.update_item.side_effect = self.conflict()
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.create_profile("one", PASSWORD)
        condition = self.table.update_item.call_args.kwargs["ConditionExpression"]
        self.assertEqual(condition, Attr("nickname").not_exists())

    def test_operator_can_only_change_an_existing_credentialless_dynamo_record(self) -> None:
        self.assertIsNone(chat_profiles.set_legacy_password("legacy", PASSWORD))
        request = self.table.update_item.call_args.kwargs
        condition = Attr("nickname").exists() & Attr("credential").not_exists()
        self.assertEqual(request["ConditionExpression"], condition)
        self.assertNotIn("profile", request["ExpressionAttributeNames"].values())
        self.table.update_item.side_effect = self.conflict()
        with self.assertRaises(ValueError):
            chat_profiles.set_legacy_password("legacy", PASSWORD)

    def test_authentication_and_session_are_bound_to_current_credential(self) -> None:
        session = self.register()
        self.table.get_item.return_value = {"Item": {"credential": self.credential, "profile": EMPTY}}
        self.assertEqual(chat_profiles.load_profile(chat_profiles.sign_in("one", PASSWORD)), EMPTY)
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.sign_in("one", "wrong passphrase")
        self.table.get_item.return_value = {}
        with self.assertRaises(chat_profiles.ProfileAuthError):
            chat_profiles.update_profile(session, add_skills=["Git"])
        self.table.update_item.assert_not_called()

    def test_conflict_reapplies_only_requested_fields_to_the_latest_profile(self) -> None:
        session = self.register()
        concurrent = {"job": None, "skills": ["Git"], "experiences": ["API 배포"], "levels": {}}
        self.table.get_item.side_effect = [{"Item": {"credential": self.credential}},
                                              {"Item": {"credential": self.credential, "profile": concurrent}}]
        self.table.update_item.side_effect = [self.conflict(), {}]
        updated = chat_profiles.update_profile(session, add_skills=iter(["Docker"]))
        self.assertEqual(updated, {**concurrent, "skills": ["Git", "Docker"]})
        first, second = self.table.update_item.call_args_list
        owner = Attr("credential").eq(self.credential)
        self.assertEqual(first.kwargs["ConditionExpression"], owner & Attr("profile").not_exists())
        self.assertEqual(second.kwargs["ConditionExpression"], owner & Attr("profile").eq(concurrent))
        self.assertIn(updated, second.kwargs["ExpressionAttributeValues"].values())

    def test_dynamo_retains_the_original_retired_answer_on_each_write(self) -> None:
        session = self.register()
        saved = {"levels": {"AB17-2": Decimal("4.0"), "C5-2": Decimal("3")}}
        for change in ({"add_skills": ["Git"]}, {"levels": {"C5-2": 0}}):
            self.table.get_item.return_value = {"Item": {"credential": self.credential, "profile": saved}}
            result = chat_profiles.update_profile(session, **change)
            self.assertNotIn("AB17-2", result["levels"])
            saved = self.table.update_item.call_args.kwargs["ExpressionAttributeValues"][":updated"]
            self.assertEqual(saved["levels"]["AB17-2"].as_tuple(), Decimal("4.0").as_tuple())
        self.assertEqual(saved["skills"], ["Git"])
        self.assertEqual(saved["levels"]["C5-2"], 0)

    def test_duplicate_save_and_failed_write_are_distinguishable(self) -> None:
        session = self.register()
        current = {"job": None, "skills": ["Git"], "experiences": [], "levels": {}}
        self.table.get_item.return_value = {"Item": {"credential": self.credential, "profile": current}}
        self.assertEqual(chat_profiles.update_profile(session, add_skills=["Git"]), current)
        self.table.update_item.assert_not_called()
        self.table.update_item.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            chat_profiles.update_profile(session, remove_skills=["Git"])
        self.table.get_item.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            chat_profiles.load_profile(session)


if __name__ == "__main__":
    unittest.main()
