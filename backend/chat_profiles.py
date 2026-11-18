"""명시적으로 저장한 상담 정보만 닉네임별로 보존한다."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path
from typing import TypedDict

from backend.api import is_mock
from backend.dynamodb import _resource
from backend.levels import validate_levels


class Profile(TypedDict):
    job: str | None
    skills: list[str]
    experiences: list[str]
    levels: dict[str, int]  # 보유 수준 진단 {세부 능력 id: 0~5} (backend/levels.py). 예전 항목은 빈 표


def _profile(item: dict) -> Profile:
    job = item.get("job")
    skills = item.get("skills", [])
    experiences = item.get("experiences", [])
    if job is not None and not isinstance(job, str):
        raise ValueError("저장된 프로필의 직무 형식이 잘못되었습니다.")
    for values in (skills, experiences):
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError("저장된 프로필의 목록 형식이 잘못되었습니다.")
    levels = item.get("levels", {})
    if not isinstance(levels, dict):
        raise ValueError("저장된 프로필의 수준 형식이 잘못되었습니다.")
    try:  # DynamoDB 는 숫자를 Decimal 로 돌려준다
        levels = validate_levels({k: int(v) for k, v in levels.items()})
    except (TypeError, ValueError) as exc:
        raise ValueError("저장된 프로필의 수준 형식이 잘못되었습니다.") from exc
    return {"job": job, "skills": skills, "experiences": experiences, "levels": levels}


def _local_profile(nickname: str, edit: Callable[[Profile], Profile] | None) -> Profile:
    override = os.getenv("SKILLPATH_MOCK_PROFILE_DB")
    data_home = Path(os.getenv("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local" / "share"
    path = Path(override) if override else data_home / "skillpath-ai" / "chat-profiles.sqlite3"

    if edit is None:
        try:
            path.stat()
        except FileNotFoundError:
            return _profile({})
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.name == "posix" and os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

    mode = "ro" if edit is None else "rw"
    uri = f"{path.absolute().as_uri()}?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True)) as connection, connection:
        if edit is None:
            # A first writer may have opened the file but not committed its schema yet.
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='profiles'"
            ).fetchone():
                return _profile({})
        else:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS profiles "
                "(nickname TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
        row = connection.execute(
            "SELECT payload FROM profiles WHERE nickname = ?", (nickname,)
        ).fetchone()
        current = _profile(json.loads(row[0]) if row else {})
        if edit is None:
            return current
        updated = edit(current)
        if current != updated:
            connection.execute(
                "INSERT INTO profiles (nickname, payload) VALUES (?, ?) "
                "ON CONFLICT(nickname) DO UPDATE SET payload=excluded.payload",
                (nickname, json.dumps(updated, ensure_ascii=False)),
            )
        return updated


def _dynamo_profile(nickname: str, edit: Callable[[Profile], Profile] | None) -> Profile:
    from boto3.dynamodb.conditions import Attr

    name = os.getenv("DYNAMODB_PROFILE_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_PROFILE_TABLE을 설정하세요.")
    table = _resource().Table(name)
    key = {"nickname": nickname}
    for _ in range(5):
        item = table.get_item(
            Key=key, ConsistentRead=True, ProjectionExpression="#profile",
            ExpressionAttributeNames={"#profile": "profile"},
        ).get("Item", {})
        saved = item.get("profile", {})
        current = _profile(saved)
        if edit is None:
            return current
        updated = edit(current)
        if updated == current:
            return current
        condition = Attr("profile").eq(saved) if "profile" in item else Attr("profile").not_exists()
        try:
            table.update_item(
                Key=key, UpdateExpression="SET #profile = :updated", ConditionExpression=condition,
                ExpressionAttributeNames={"#profile": "profile"},
                ExpressionAttributeValues={":updated": {**saved, **updated}},
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            continue
        return updated
    raise RuntimeError("프로필이 동시에 변경되어 저장하지 못했습니다. 다시 시도하세요.")


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("프로필 항목에는 비어 있지 않은 문자열이 필요합니다.")
    return value.strip()


def _values(values: Iterable[str]) -> list[str]:
    if isinstance(values, str):
        raise ValueError("프로필 항목은 문자열 목록으로 전달하세요.")
    return [_text(value) for value in values]


def _changed_list(existing: list[str], additions: list[str], removals: list[str]) -> list[str]:
    excluded = {value.casefold() for value in removals}
    result = [value for value in existing if value.casefold() not in excluded]
    seen = excluded.union(value.casefold() for value in result)
    for value in additions:
        key = value.casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def load_profile(nickname: str) -> Profile:
    """닉네임에 저장한 정보만 읽는다. 처음 조회한 닉네임은 빈 프로필로 반환한다."""
    store = _local_profile if is_mock() else _dynamo_profile
    return store(_text(nickname), None)


def update_profile(
    nickname: str, *, job: str | None = None, add_skills: Iterable[str] = (),
    remove_skills: Iterable[str] = (), add_experiences: Iterable[str] = (),
    remove_experiences: Iterable[str] = (), levels: dict[str, int] | None = None,
) -> Profile:
    """명시된 항목을 한 번에 변경한다. 같은 항목을 추가하고 제거하면 제거를 우선한다.

    levels 는 보유 수준 진단 결과 {세부 능력 id: 0~5} 이다. 준 세부 능력만 덮어쓰고 나머지는 그대로 둔다.
    """
    new_levels = validate_levels(levels) if levels else {}
    job = _text(job) if job is not None else None
    new_skills, old_skills = _values(add_skills), _values(remove_skills)
    new_experiences, old_experiences = _values(add_experiences), _values(remove_experiences)
    if not any((job, new_skills, old_skills, new_experiences, old_experiences, new_levels)):
        return load_profile(nickname)

    def edit(current: Profile) -> Profile:
        return {
            "job": job if job is not None else current["job"],
            "skills": _changed_list(current["skills"], new_skills, old_skills),
            "experiences": _changed_list(current["experiences"], new_experiences, old_experiences),
            "levels": {**current["levels"], **new_levels},
        }

    store = _local_profile if is_mock() else _dynamo_profile
    return store(_text(nickname), edit)
