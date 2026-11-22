"""공고별 최신 추출 결과와 완성된 직무 수요 집계를 DynamoDB에 저장한다."""
from __future__ import annotations

import argparse
import os
from decimal import Decimal
from typing import Any

DEFAULT_REGION = "ap-northeast-2"


def table_names() -> tuple[str, str]:
    """환경 설정의 테이블 이름을 검증한다."""
    extractions = os.getenv("DYNAMODB_EXTRACTIONS_TABLE", "").strip()
    demand = os.getenv("DYNAMODB_DEMAND_TABLE", "").strip()
    if not extractions or not demand:
        raise ValueError("DYNAMODB_EXTRACTIONS_TABLE과 DYNAMODB_DEMAND_TABLE을 설정하세요.")
    if extractions == demand:
        raise ValueError("추출 결과와 수요 집계에는 서로 다른 테이블 이름이 필요합니다.")
    return extractions, demand


def _resource():
    import boto3

    return boto3.resource(
        "dynamodb", region_name=os.getenv("AWS_REGION", DEFAULT_REGION)
    )


def json_numbers(value: Any) -> Any:
    """DynamoDB에서 읽은 정수 Decimal을 JSON과 계산에 쓸 int로 바꾼다."""
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("수요 집계에 정수가 아닌 숫자가 있습니다.")
        return int(value)
    if isinstance(value, dict):
        return {key: json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_numbers(item) for item in value]
    return value


class DynamoDBStore:
    """두 테이블의 기본 키를 사용해 성공 결과와 수요 스냅샷을 읽고 쓴다."""

    def __init__(self, extractions_table, demand_table):
        self.extractions_table = extractions_table
        self.demand_table = demand_table

    @classmethod
    def from_env(cls, *, resource=None) -> DynamoDBStore:
        extractions, demand = table_names()
        dynamodb = resource if resource is not None else _resource()
        return cls(dynamodb.Table(extractions), dynamodb.Table(demand))

    def get_extraction(self, job_category: str, posting_id: str) -> dict | None:
        snapshot = self.get_demand_snapshot(job_category) or {}
        partition = snapshot.get("extraction_partition", job_category)
        row = self.get_partition_extraction(partition, posting_id)
        if row is not None and partition != job_category:
            return {**row, "job_category": job_category}
        return row

    def get_partition_extraction(self, partition: str, posting_id: str) -> dict | None:
        response = self.extractions_table.get_item(
            Key={"job_category": partition, "posting_id": posting_id},
            ConsistentRead=True,
        )
        return response.get("Item")

    def save_extraction(self, item: dict) -> None:
        if str(item.get("job_category", "")).startswith("run#"):
            raise ValueError("Versioned extractions can only be created with stage_extraction.")
        self.extractions_table.put_item(Item=item)

    def list_extractions(self, job_category: str) -> list[dict]:
        return self.snapshot_extractions(job_category, self.get_demand_snapshot(job_category))

    def list_partition_extractions(self, partition: str) -> list[dict]:
        """직무 partition의 모든 페이지를 강한 일관성으로 읽는다."""
        request = {
            "KeyConditionExpression": "#category = :category",
            "ExpressionAttributeNames": {"#category": "job_category"},
            "ExpressionAttributeValues": {":category": partition},
            "ConsistentRead": True,
        }
        items = []
        while True:
            response = self.extractions_table.query(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            request["ExclusiveStartKey"] = last_key

    def save_demand(self, snapshot: dict) -> None:
        self.demand_table.put_item(Item=snapshot)

    def snapshot_extractions(self, job_category: str, snapshot: dict | None) -> list[dict]:
        """Resolve the published partition, including snapshots created by the CLI."""
        partition = (snapshot or {}).get("extraction_partition", job_category)
        rows = self.list_partition_extractions(partition)
        if partition == job_category:
            return rows
        return [{**row, "job_category": job_category} for row in rows]

    def stage_extraction(self, partition: str, item: dict) -> None:
        """Keep the first successful extraction of this input in this run."""
        from boto3.dynamodb.conditions import Attr

        try:
            self.extractions_table.put_item(
                Item={**item, "job_category": partition},
                ConditionExpression=Attr("posting_id").not_exists(),
            )
        except self.extractions_table.meta.client.exceptions.ConditionalCheckFailedException:
            existing = self.get_partition_extraction(partition, item["posting_id"])
            if (not existing or existing["input_fingerprint"] != item["input_fingerprint"]
                    or existing["posting_s3_key"] != item["posting_s3_key"]):
                raise ValueError("Conflicting staged extraction for this run.")

    def publish_batch(self, snapshot: dict, expected_base: str | None) -> None:
        """Swap the snapshot and its extraction partition in one conditional write."""
        from boto3.dynamodb.conditions import Attr

        condition = (Attr("job_category").not_exists() if expected_base is None
                     else Attr("snapshot_id").eq(expected_base))
        try:
            self.demand_table.put_item(Item=snapshot, ConditionExpression=condition)
        except self.demand_table.meta.client.exceptions.ConditionalCheckFailedException:
            current = self.get_demand_snapshot(snapshot["job_category"]) or {}
            if current.get("snapshot_id") != snapshot["snapshot_id"]:
                raise ValueError("The published snapshot changed before finalization.")

    def get_demand_snapshot(self, job_category: str) -> dict | None:
        response = self.demand_table.get_item(
            Key={"job_category": job_category}, ConsistentRead=True
        )
        return response.get("Item")


def _ensure_table(client, name: str, keys: list[dict]) -> None:
    """이미 있는 테이블은 키를 검사하고 새 테이블은 온디맨드로 만든다."""
    from botocore.exceptions import ClientError

    try:
        description = client.describe_table(TableName=name)["Table"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        try:
            client.create_table(
                TableName=name,
                KeySchema=keys,
                AttributeDefinitions=[
                    {"AttributeName": key["AttributeName"], "AttributeType": "S"}
                    for key in keys
                ],
                BillingMode="PAY_PER_REQUEST",
            )
        except ClientError as create_error:
            if create_error.response["Error"]["Code"] != "ResourceInUseException":
                raise
        description = None

    if description is None or description.get("TableStatus") != "ACTIVE":
        client.get_waiter("table_exists").wait(TableName=name)
        description = client.describe_table(TableName=name)["Table"]
    actual_keys = {key["AttributeName"]: key["KeyType"] for key in description["KeySchema"]}
    expected_keys = {key["AttributeName"]: key["KeyType"] for key in keys}
    types = {
        field["AttributeName"]: field["AttributeType"]
        for field in description["AttributeDefinitions"]
    }
    if actual_keys != expected_keys or any(types.get(key) != "S" for key in expected_keys):
        raise ValueError(f"기존 테이블의 기본 키가 설계와 다릅니다: {name}")
    billing = description.get("BillingModeSummary", {}).get("BillingMode")
    if billing != "PAY_PER_REQUEST":
        raise ValueError(f"기존 테이블의 요금 모드가 PAY_PER_REQUEST가 아닙니다: {name}")


def create_tables(*, resource=None) -> tuple[str, str]:
    """환경 설정의 두 테이블을 생성하거나 기존 정의를 확인한다."""
    extractions, demand = table_names()
    dynamodb = resource if resource is not None else _resource()
    client = dynamodb.meta.client
    _ensure_table(client, extractions, [
        {"AttributeName": "job_category", "KeyType": "HASH"},
        {"AttributeName": "posting_id", "KeyType": "RANGE"},
    ])
    _ensure_table(client, demand, [
        {"AttributeName": "job_category", "KeyType": "HASH"},
    ])
    return extractions, demand


def create_profile_table(*, resource=None) -> str:
    """Create or validate the application's profile table independently of the batch tables."""
    name = os.getenv("DYNAMODB_PROFILE_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_PROFILE_TABLE을 설정하세요.")
    if name in (os.getenv("DYNAMODB_EXTRACTIONS_TABLE", "").strip(), os.getenv("DYNAMODB_DEMAND_TABLE", "").strip()):
        raise ValueError("프로필과 공고 데이터에는 서로 다른 테이블 이름이 필요합니다.")
    dynamodb = resource if resource is not None else _resource()
    _ensure_table(dynamodb.meta.client, name, [{"AttributeName": "nickname", "KeyType": "HASH"}])
    return name


def _ensure_ttl(client, name: str, attribute: str) -> None:
    """TTL 이 꺼져 있으면 켠다. 이미 켜졌거나 켜지는 중이면 속성 이름만 확인한다."""
    spec = client.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
    if spec.get("TimeToLiveStatus") in ("ENABLED", "ENABLING"):
        if spec.get("AttributeName") != attribute:
            raise ValueError(f"기존 테이블의 TTL 속성이 {attribute}이(가) 아닙니다: {name}")
        return
    client.update_time_to_live(
        TableName=name, TimeToLiveSpecification={"Enabled": True, "AttributeName": attribute})


def create_chat_table(*, resource=None) -> str:
    """회원 대화 기록 테이블(backend/chat_history.py)을 만들거나 확인하고, 만료 시각 속성으로 TTL 을 켠다."""
    name = os.getenv("DYNAMODB_CHAT_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_CHAT_TABLE을 설정하세요.")
    others = {os.getenv(key, "").strip() for key in (
        "DYNAMODB_EXTRACTIONS_TABLE", "DYNAMODB_DEMAND_TABLE", "DYNAMODB_PROFILE_TABLE")} - {""}
    if name in others:
        raise ValueError("대화 기록에는 다른 데이터와 다른 테이블 이름이 필요합니다.")
    client = (resource if resource is not None else _resource()).meta.client
    _ensure_table(client, name, [
        {"AttributeName": "nickname", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ])
    _ensure_ttl(client, name, "expires_at")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create-tables", "create-profile-table", "create-chat-table"])
    args = parser.parse_args()
    from pipeline.rocketpunch import load_env_file

    load_env_file()
    names = {"create-profile-table": lambda: [create_profile_table()],
             "create-chat-table": lambda: [create_chat_table()],
             "create-tables": lambda: create_tables()}[args.command]()
    for name in names:
        print(f"테이블 준비 완료 {name}")


if __name__ == "__main__":
    main()
