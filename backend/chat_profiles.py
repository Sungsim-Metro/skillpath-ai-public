"""명시적으로 저장한 상담 정보만 닉네임별로 보존한다."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from backend.api import is_mock
from backend.dynamodb import _resource
from backend.levels import LEVELS, level_guide, merge_levels, validate_levels
from backend.profile_auth import (
    ProfileAuthError,
    ProfileSession,
    check_credential,
    issue_session,
    login_password_bytes,
    new_credential,
    nickname_text,
    require_session,
    verify_password,
)
from backend.profile_auth_limits import clear_login_attempts, reserve_login_attempt


class Profile(TypedDict):
    job: str | None
    skills: list[str]
    experiences: list[str]
    levels: dict[str, int]  # 보유 수준 진단 {세부 능력 id: 0~5} (backend/levels.py). 예전 항목은 빈 표


def _stored_levels(raw: object) -> tuple[dict[str, int], dict[str, int | Decimal]]:
    """Return current answers and original values for well-formed retired survey IDs."""
    if not isinstance(raw, dict):
        raise ValueError("저장된 프로필의 수준 형식이 잘못되었습니다.")
    questions = level_guide()["subskills"]
    active: dict[str, int] = {}
    retired: dict[str, int | Decimal] = {}
    for key, value in raw.items():
        numeric = type(value) is int or (isinstance(value, Decimal) and value.is_finite())
        if not numeric or value not in LEVELS:
            raise ValueError("저장된 프로필의 수준 형식이 잘못되었습니다.")
        if isinstance(key, str) and key not in questions and re.fullmatch(r"[A-Z]+[0-9]+-[0-9]+", key):
            retired[key] = value
        else:
            active[key] = int(value)
    return validate_levels(active), retired


def _edit_profile(item: dict, edit: Callable[[Profile], Profile] | None) -> tuple[Profile, dict | None]:
    """Expose active answers to callers and retain retired answers on every write."""
    job = item.get("job")
    skills = item.get("skills", [])
    experiences = item.get("experiences", [])
    if job is not None and not isinstance(job, str):
        raise ValueError("저장된 프로필의 직무 형식이 잘못되었습니다.")
    for values in (skills, experiences):
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError("저장된 프로필의 목록 형식이 잘못되었습니다.")
    levels, retired = _stored_levels(item.get("levels", {}))
    current: Profile = {"job": job, "skills": skills, "experiences": experiences, "levels": levels}
    updated = edit(current) if edit is not None else current
    if updated == current:
        return current, None
    return updated, {**item, **updated, "levels": {**retired, **updated["levels"]}}


@contextmanager
def _local_connection(*, create: bool = False, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    """Open an existing store in the requested mode; only registration creates it."""
    override = os.getenv("SKILLPATH_MOCK_PROFILE_DB")
    data_home = Path(os.getenv("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local" / "share"
    path = Path(override) if override else data_home / "skillpath-ai" / "chat-profiles.sqlite3"

    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not path.is_file():
        yield None
        return
    if write:
        flags = os.O_WRONLY | (os.O_CREAT if create else 0)
        fd = os.open(path, flags, 0o600)
        try:
            if os.name == "posix" and os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

    mode = "rw" if write else "ro"
    uri = f"{path.absolute().as_uri()}?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True)) as connection, connection:
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(profiles)")}


def _dynamo_table():
    name = os.getenv("DYNAMODB_PROFILE_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_PROFILE_TABLE을 설정하세요.")
    return _resource().Table(name)


def _assign_credential(nickname: str, credential: str, *, legacy: bool) -> None:
    """Claim a new nickname, or activate an operator-verified legacy record."""
    unavailable = "기존 프로필의 비밀번호를 설정할 수 없습니다." if legacy else "이 닉네임으로 가입할 수 없습니다."
    if is_mock():
        with _local_connection(create=not legacy, write=True) as connection:
            if connection is None:
                raise ProfileAuthError(unavailable)
            columns = _columns(connection)
            if not columns:
                if legacy:
                    raise ProfileAuthError(unavailable)
                connection.execute(
                    "CREATE TABLE profiles (nickname TEXT PRIMARY KEY, payload TEXT NOT NULL, credential TEXT)"
                )
            elif "credential" not in columns:
                connection.execute("ALTER TABLE profiles ADD COLUMN credential TEXT")
            if legacy:
                cursor = connection.execute(
                    "UPDATE profiles SET credential = ? WHERE nickname = ? AND credential IS NULL",
                    (credential, nickname),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO profiles (nickname, payload, credential) VALUES (?, '{}', ?) "
                    "ON CONFLICT(nickname) DO NOTHING", (nickname, credential),
                )
            if cursor.rowcount != 1:
                raise ProfileAuthError(unavailable)
        return

    from boto3.dynamodb.conditions import Attr

    table = _dynamo_table()
    condition = (Attr("nickname").exists() & Attr("credential").not_exists()
                 if legacy else Attr("nickname").not_exists())
    try:
        table.update_item(
            Key={"nickname": nickname}, UpdateExpression="SET #credential = :credential",
            ExpressionAttributeNames={"#credential": "credential"},
            ExpressionAttributeValues={":credential": credential}, ConditionExpression=condition,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        raise ProfileAuthError(unavailable) from None


def create_profile(nickname: str, password: str) -> ProfileSession:
    """Register a new account atomically without acquiring an existing nickname."""
    nickname = nickname_text(nickname)
    credential = new_credential(password)
    _assign_credential(nickname, credential, legacy=False)
    return issue_session(nickname, credential)


def set_legacy_password(nickname: str, password: str) -> None:
    """Operator-only activation after verifying ownership outside this application."""
    _assign_credential(nickname_text(nickname), new_credential(password), legacy=True)


def sign_in(nickname: str, password: str) -> ProfileSession:
    """Verify the password without fetching or returning profile contents."""
    nickname = nickname_text(nickname)
    encoded = login_password_bytes(password)
    if not reserve_login_attempt(nickname):
        raise ProfileAuthError("로그인 시도가 많습니다. 잠시 후 다시 시도하세요.")
    if is_mock():
        with _local_connection() as connection:
            row = None
            if connection is not None and "credential" in _columns(connection):
                row = connection.execute("SELECT credential FROM profiles WHERE nickname = ?", (nickname,)).fetchone()
            credential = row[0] if row else None
    else:
        item = _dynamo_table().get_item(
            Key={"nickname": nickname}, ConsistentRead=True, ProjectionExpression="#credential",
            ExpressionAttributeNames={"#credential": "credential"},
        ).get("Item", {})
        credential = item.get("credential")
    verify_password(encoded, credential)
    clear_login_attempts(nickname)
    return issue_session(nickname, credential)


def _local_profile(session: ProfileSession, edit: Callable[[Profile], Profile] | None) -> Profile:
    with _local_connection(write=edit is not None) as connection:
        if connection is None or "credential" not in _columns(connection):
            raise ProfileAuthError("다시 로그인해 주세요.")
        row = connection.execute(
            "SELECT credential, payload FROM profiles WHERE nickname = ?", (session.nickname,)
        ).fetchone()
        check_credential(session, row[0] if row else None)
        profile, payload = _edit_profile(json.loads(row[1]), edit)
        if payload is not None:
            connection.execute(
                "UPDATE profiles SET payload = ? WHERE nickname = ?",
                (json.dumps(payload, ensure_ascii=False), session.nickname),
            )
        return profile


def _dynamo_profile(session: ProfileSession, edit: Callable[[Profile], Profile] | None) -> Profile:
    from boto3.dynamodb.conditions import Attr

    table = _dynamo_table()
    key = {"nickname": session.nickname}
    for _ in range(5):
        item = table.get_item(
            Key=key, ConsistentRead=True, ProjectionExpression="#credential, #profile",
            ExpressionAttributeNames={"#credential": "credential", "#profile": "profile"},
        ).get("Item", {})
        credential = item.get("credential")
        check_credential(session, credential)
        saved = item.get("profile", {})
        profile, payload = _edit_profile(saved, edit)
        if payload is None:
            return profile
        condition = Attr("profile").eq(saved) if "profile" in item else Attr("profile").not_exists()
        try:
            table.update_item(
                Key=key, UpdateExpression="SET #profile = :updated",
                ConditionExpression=Attr("credential").eq(credential) & condition,
                ExpressionAttributeNames={"#profile": "profile"},
                ExpressionAttributeValues={":updated": payload},
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            continue
        return profile
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


def load_profile(session: ProfileSession) -> Profile:
    """Read the profile owned by an authenticated session."""
    require_session(session)
    store = _local_profile if is_mock() else _dynamo_profile
    return store(session, None)


def update_profile(
    session: ProfileSession, *, job: str | None = None, add_skills: Iterable[str] = (),
    remove_skills: Iterable[str] = (), add_experiences: Iterable[str] = (),
    remove_experiences: Iterable[str] = (), levels: dict[str, int] | None = None,
) -> Profile:
    """명시된 항목을 한 번에 변경한다. 같은 항목을 추가하고 제거하면 제거를 우선한다.

    levels 는 보유 수준 진단 결과 {세부 능력 id: 0~5} 이다. 준 세부 능력만 덮어쓰고 나머지는 그대로 둔다.
    """
    require_session(session)
    new_levels = validate_levels(levels) if levels else {}
    job = _text(job) if job is not None else None
    new_skills, old_skills = _values(add_skills), _values(remove_skills)
    new_experiences, old_experiences = _values(add_experiences), _values(remove_experiences)
    if not any((job, new_skills, old_skills, new_experiences, old_experiences, new_levels)):
        return load_profile(session)

    def edit(current: Profile) -> Profile:
        return {
            "job": job if job is not None else current["job"],
            "skills": _changed_list(current["skills"], new_skills, old_skills),
            "experiences": _changed_list(current["experiences"], new_experiences, old_experiences),
            "levels": merge_levels(current["levels"], new_levels),
        }

    store = _local_profile if is_mock() else _dynamo_profile
    return store(session, edit)
