"""DynamoDB 스키마, 페이지 처리와 오류 전파를 모의 SDK로 확인한다."""
from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from backend import api
from backend.dynamodb import DynamoDBStore, create_chat_table, create_profile_table, create_tables, json_numbers


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test"}}, "test")


def description(sort_key: bool = False) -> dict:
    keys = [{"AttributeName": "job_category", "KeyType": "HASH"}]
    if sort_key:
        keys.append({"AttributeName": "posting_id", "KeyType": "RANGE"})
    return {"Table": {
        "TableStatus": "ACTIVE",
        "KeySchema": keys,
        "AttributeDefinitions": [
            {"AttributeName": key["AttributeName"], "AttributeType": "S"}
            for key in keys
        ],
        "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
    }}


class DynamoDBStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.extractions = Mock()
        self.demand = Mock()
        self.store = DynamoDBStore(self.extractions, self.demand)

    def test_query_reads_all_pages_consistently(self) -> None:
        last_key = {"job_category": "cloud_engineer", "posting_id": "rp-1"}
        self.extractions.query.side_effect = [
            {"Items": [{"posting_id": "rp-1"}], "LastEvaluatedKey": last_key},
            {"Items": [{"posting_id": "rp-2"}]},
        ]
        result = self.store.list_extractions("cloud_engineer")
        self.assertEqual([item["posting_id"] for item in result], ["rp-1", "rp-2"])
        calls = self.extractions.query.call_args_list
        self.assertTrue(all(call.kwargs["ConsistentRead"] for call in calls))
        self.assertNotIn("ExclusiveStartKey", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["ExclusiveStartKey"], last_key)
        self.assertEqual(calls[0].kwargs["ExpressionAttributeValues"], {":category": "cloud_engineer"})

    def test_gets_use_the_expected_keys_and_consistent_reads(self) -> None:
        self.extractions.get_item.return_value = {"Item": {"posting_id": "rp-1"}}
        self.demand.get_item.return_value = {}
        self.assertEqual(self.store.get_extraction("cloud_engineer", "rp-1"), {"posting_id": "rp-1"})
        self.extractions.get_item.assert_called_once_with(
            Key={"job_category": "cloud_engineer", "posting_id": "rp-1"}, ConsistentRead=True
        )
        self.assertIsNone(self.store.get_demand_snapshot("cloud_engineer"))
        self.demand.get_item.assert_called_once_with(
            Key={"job_category": "cloud_engineer"}, ConsistentRead=True
        )

    def test_put_failures_propagate_without_retrying_other_writes(self) -> None:
        self.extractions.put_item.side_effect = client_error("ValidationException")
        self.demand.put_item.side_effect = client_error("AccessDeniedException")
        with self.assertRaises(ClientError):
            self.store.save_extraction({"posting_id": "rp-1"})
        with self.assertRaises(ClientError):
            self.store.save_demand({"job_category": "cloud_engineer"})
        self.assertEqual(self.extractions.put_item.call_count, 1)
        self.assertEqual(self.demand.put_item.call_count, 1)

    def test_missing_table_configuration_fails_before_sdk_creation(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("backend.dynamodb._resource") as resource:
                with self.assertRaises(ValueError):
                    DynamoDBStore.from_env()
                resource.assert_not_called()

    def test_create_tables_is_idempotent_and_uses_on_demand_keys(self) -> None:
        resource = Mock()
        client = resource.meta.client
        client.describe_table.side_effect = [
            client_error("ResourceNotFoundException"), description(True),
            client_error("ResourceNotFoundException"), description(False),
        ]
        with patch.dict(os.environ, {
            "DYNAMODB_EXTRACTIONS_TABLE": "test-extractions", "DYNAMODB_DEMAND_TABLE": "test-demand",
        }, clear=True):
            self.assertEqual(create_tables(resource=resource), ("test-extractions", "test-demand"))
            self.assertEqual(client.create_table.call_count, 2)
            for call in client.create_table.call_args_list:
                self.assertEqual(call.kwargs["BillingMode"], "PAY_PER_REQUEST")
                self.assertTrue(all(a["AttributeType"] == "S" for a in call.kwargs["AttributeDefinitions"]))
            client.create_table.reset_mock()
            client.describe_table.side_effect = [description(True), description(False)]
            create_tables(resource=resource)
            client.create_table.assert_not_called()

    def test_existing_incompatible_table_is_rejected(self) -> None:
        resource = Mock()
        resource.meta.client.describe_table.return_value = description(False)
        with patch.dict(os.environ, {
            "DYNAMODB_EXTRACTIONS_TABLE": "test-extractions", "DYNAMODB_DEMAND_TABLE": "test-demand",
        }, clear=True):
            with self.assertRaises(ValueError):
                create_tables(resource=resource)
        resource.meta.client.create_table.assert_not_called()

    def test_profile_table_needs_only_its_own_setting(self) -> None:
        resource = Mock()
        client = resource.meta.client
        keys = [{"AttributeName": "nickname", "KeyType": "HASH"}]
        active = description()
        active["Table"]["KeySchema"] = keys
        active["Table"]["AttributeDefinitions"] = [{"AttributeName": "nickname", "AttributeType": "S"}]
        client.describe_table.side_effect = [client_error("ResourceNotFoundException"), active, active]
        with patch.dict(os.environ, {"DYNAMODB_PROFILE_TABLE": "profiles"}, clear=True):
            self.assertEqual(create_profile_table(resource=resource), "profiles")
            self.assertEqual(create_profile_table(resource=resource), "profiles")
        self.assertEqual(client.create_table.call_count, 1)
        self.assertEqual(client.create_table.call_args.kwargs["KeySchema"], keys)

    def test_chat_table_has_a_sort_key_and_turns_on_ttl_once(self) -> None:
        resource = Mock()
        client = resource.meta.client
        keys = [{"AttributeName": "nickname", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]
        active = description()
        active["Table"]["KeySchema"] = keys
        active["Table"]["AttributeDefinitions"] = [
            {"AttributeName": "nickname", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}]
        client.describe_table.side_effect = [client_error("ResourceNotFoundException"), active, active]
        off = {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}}
        on = {"TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED", "AttributeName": "expires_at"}}
        client.describe_time_to_live.side_effect = [off, on]
        with patch.dict(os.environ, {"DYNAMODB_CHAT_TABLE": "chat"}, clear=True):
            self.assertEqual(create_chat_table(resource=resource), "chat")
            self.assertEqual(create_chat_table(resource=resource), "chat")
        self.assertEqual(client.create_table.call_count, 1)
        self.assertEqual(client.create_table.call_args.kwargs["KeySchema"], keys)
        client.update_time_to_live.assert_called_once_with(
            TableName="chat", TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"})

    def test_chat_table_rejects_another_tables_name_and_a_wrong_ttl_attribute(self) -> None:
        resource = Mock()
        with patch.dict(os.environ, {"DYNAMODB_CHAT_TABLE": "same", "DYNAMODB_PROFILE_TABLE": "same"}, clear=True):
            with self.assertRaises(ValueError):
                create_chat_table(resource=resource)
        resource.meta.client.create_table.assert_not_called()
        keys = [{"AttributeName": "nickname", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]
        active = description()
        active["Table"]["KeySchema"] = keys
        active["Table"]["AttributeDefinitions"] = [
            {"AttributeName": "nickname", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}]
        resource.meta.client.describe_table.return_value = active
        resource.meta.client.describe_time_to_live.return_value = {
            "TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED", "AttributeName": "other"}}
        with patch.dict(os.environ, {"DYNAMODB_CHAT_TABLE": "chat"}, clear=True):
            with self.assertRaises(ValueError):
                create_chat_table(resource=resource)

    def test_profile_table_rejects_a_reserved_pipeline_table_name_before_creation(self) -> None:
        resource = Mock()
        with patch.dict(os.environ, {"DYNAMODB_PROFILE_TABLE": "demand", "DYNAMODB_DEMAND_TABLE": "demand"}):
            with self.assertRaisesRegex(ValueError, "다른 테이블"):
                create_profile_table(resource=resource)
        resource.meta.client.describe_table.assert_not_called()

    def test_generated_extraction_and_snapshot_use_sdk_serializable_types(self) -> None:
        from pathlib import Path

        from boto3.dynamodb.types import TypeSerializer

        from ai.extract import extraction_settings, input_fingerprint, to_contract
        from backend.demand import build_snapshot
        from pipeline.extraction_batch import BatchInput, _extraction_item

        posting = {
            "id": "rp-1", "role": "클라우드 엔지니어", "text": "Docker experience",
            "title": "Cloud Engineer", "company": "Example", "url": None, "posted_at": None,
        }
        with patch.dict(os.environ, {"BEDROCK_MODEL_ID": "test-model"}, clear=True):
            settings = extraction_settings()
            fingerprint = input_fingerprint(posting, "cloud_engineer", settings)
        analysis = {
            **settings,
            "extracted_at": "2026-11-15T03:00:00Z",
            "usage": {"inputTokens": 20, "outputTokens": 10, "totalTokens": 30},
        }
        extraction = {
            "posting_id": posting["id"],
            "skills": to_contract([{
                "raw": "Docker", "normalized": "Docker", "type": "required",
                "evidence": "Docker experience",
            }], posting["text"]),
        }
        batch_input = BatchInput(
            Path("rp-1.posting.json"), b"{}", posting,
            "processed/postings/rp-1/test.json", fingerprint,
        )
        item = _extraction_item(posting, batch_input, "cloud_engineer", extraction, analysis)
        snapshot = build_snapshot(
            [item], selected={posting["id"]: fingerprint}, job_category="cloud_engineer",
            collected="2026-11-15", exclusions={}, selection_version="cloud-aws-v1", run_id="test-run",
        )
        serializer = TypeSerializer()
        serialized_item = serializer.serialize(item)["M"]
        serialized_snapshot = serializer.serialize(snapshot)["M"]
        inference = serialized_item["analysis"]["M"]["inference_config"]["M"]
        self.assertEqual(inference["temperature"], {"S": "0.0"})
        self.assertEqual(serialized_snapshot["demand"]["M"]["total_postings"], {"N": "1"})


class DemandReadTest(unittest.TestCase):
    def setUp(self) -> None:
        api._demand_cache.clear()
        self.addCleanup(api._demand_cache.clear)

    def test_decimal_counts_work_in_gap_area_and_json(self) -> None:
        demand = {
            "job_category": "cloud_engineer", "job_title": "클라우드 엔지니어",
            "total_postings": Decimal(1), "sources": ["rocketpunch"], "collected": "2026-11-15",
            "skills": [
                {"skill": name, "postings": Decimal(1), "required": Decimal(1),
                 "preferred": Decimal(0), "evidence_samples": []}
                for name in ("Docker", "Linux")
            ],
        }
        store = Mock()
        store.get_demand_snapshot.return_value = {"snapshot_id": "test-run", "demand": demand}
        with patch.dict(os.environ, {
            "BEDROCK_MOCK": "0", "DYNAMODB_DEMAND_TABLE": "test-demand",
        }, clear=True):
            with patch.object(api.DynamoDBStore, "from_env", return_value=store):
                result = api.get_demand("cloud_engineer")
                self.assertIs(type(result["total_postings"]), int)
                self.assertEqual(len(api.get_gap("cloud_engineer", [])), 2)
                areas = {row["area_id"]: row for row in api.get_area_gap("cloud_engineer", [])}
                self.assertEqual(areas["C1"]["demand"], 2)
                import json

                json.dumps(result)
                result["skills"].clear()
                self.assertEqual(len(api.get_demand("cloud_engineer")["skills"]), 2)
        store.get_demand_snapshot.assert_called_once_with("cloud_engineer")

    def test_cache_expires_after_sixty_seconds(self) -> None:
        store = Mock()
        store.get_demand_snapshot.side_effect = [
            {"demand": {"total_postings": Decimal(1)}},
            {"demand": {"total_postings": Decimal(2)}},
        ]
        with patch.dict(os.environ, {"BEDROCK_MOCK": "0", "DYNAMODB_DEMAND_TABLE": "test-demand"}, clear=True):
            with patch.object(api.DynamoDBStore, "from_env", return_value=store):
                with patch.object(api, "monotonic", side_effect=[0, 0, 59, 60, 60]):
                    self.assertEqual(api.get_demand("cloud_engineer")["total_postings"], 1)
                    self.assertEqual(api.get_demand("cloud_engineer")["total_postings"], 1)
                    self.assertEqual(api.get_demand("cloud_engineer")["total_postings"], 2)
        self.assertEqual(store.get_demand_snapshot.call_count, 2)

    def test_mock_mode_does_not_access_dynamodb(self) -> None:
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}, clear=True):
            with patch.object(api.DynamoDBStore, "from_env") as factory:
                self.assertEqual(api.get_demand("cloud_engineer")["total_postings"], 40)
                factory.assert_not_called()

    def test_fractional_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            json_numbers({"postings": Decimal("1.5")})


if __name__ == "__main__":
    unittest.main()
