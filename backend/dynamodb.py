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
        response = self.extractions_table.get_item(
            Key={"job_category": job_category, "posting_id": posting_id},
            ConsistentRead=True,
        )
        return response.get("Item")

    def save_extraction(self, item: dict) -> None:
        self.extractions_table.put_item(Item=item)

    def list_extractions(self, job_category: str) -> list[dict]:
        """직무 partition의 모든 페이지를 강한 일관성으로 읽는다."""
        request = {
            "KeyConditionExpression": "#category = :category",
            "ExpressionAttributeNames": {"#category": "job_category"},
            "ExpressionAttributeValues": {":category": job_category},
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create-tables"])
    parser.parse_args()
    from pipeline.rocketpunch import load_env_file

    load_env_file()
    for name in create_tables():
        print(f"테이블 준비 완료 {name}")


if __name__ == "__main__":
    main()
