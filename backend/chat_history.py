"""로그인한 회원의 상담 대화를 세션별로 보관한다(상담 화면의 대화 기록 목록용, 2026-11-20).

- 비회원(닉네임 없음)은 저장하지 않는다. 세 함수 모두 아무것도 하지 않고 빈 값을 돌려준다.
- 실제 모드에서 DYNAMODB_CHAT_TABLE 이 없으면 이 기능은 꺼진다(enabled() 가 False: 저장·목록·열람이 모두 조용히 비고, 화면도 패널과 안내를 그리지 않는다).
- 저장하기 전에 이메일·전화번호(+82 국가번호 포함)·주민등록번호·외국인등록번호 모양의 글자는 코드로 가린다. 이름 같은 것은 못 가리므로 화면에 기록 안내를 둔다.
- 저장 실패가 상담을 막지 않는다. append_turn 은 실패하면 False 를 돌려주고, 로그에는 예외 종류만 남긴다(대화·닉네임 없음).
- 실제 모드는 DynamoDB(DYNAMODB_CHAT_TABLE), 모의 모드는 로컬 SQLite(SKILLPATH_MOCK_CHAT_DB)를 쓴다. 테이블 모양은 backend/README.md.
  DynamoDB 는 파티션 키 nickname, 정렬 키 sk 이고 한 턴(질문+답)이 한 줄이다. sk 는 세션 줄 `S#<세션ID>`, 턴 줄 `T#<세션ID>#<시각>`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from backend.api import is_mock
from backend.dynamodb import _resource

logger = logging.getLogger(__name__)

Turn = tuple[str, str, list[dict]]  # (질문, 답변, 추천 과정) — app/chat.py 의 chat_history 한 줄과 같은 모양
TITLE_CHARS = 30
DEFAULT_TTL_DAYS = 30
_SESSION_ID = re.compile(r"[0-9a-f]{32}")  # ai.usage.SessionUsage.session_id (uuid4 hex)
# 순서가 중요하다: 13자리 주민번호를 전화번호보다 먼저 가린다. \w 는 한글도 글자로 보므로 숫자는 앞뒤 숫자 여부로 끊는다.
# 국내 번호는 0 으로 시작하고, 국가번호가 붙으면 +82 뒤에 앞의 0 이 빠진다: 010-XXXX-XXXX = +82 10-XXXX-XXXX = +82 (0)10-XXXX-XXXX
_KR_PREFIX = r"(?:(?<!\d)0|\+82[-.\s]?(?:\(0\))?)"
_MASKS = [
    (re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)"), "[주민번호]"),  # 주민등록번호(뒷자리 1~4)와 외국인등록번호(5~8)
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[이메일]"),
    (re.compile(rf"{_KR_PREFIX}1[016789][-.\s]?\d{{3,4}}[-.\s]?\d{{4}}(?!\d)"), "[전화번호]"),  # 휴대전화
    (re.compile(rf"{_KR_PREFIX}\d{{1,2}}[-.\s]\d{{3,4}}[-.\s]\d{{4}}(?!\d)"), "[전화번호]"),  # 지역번호
]


_clock_lock = Lock()
_last_ts = ""


def _now() -> str:
    """UTC 시각 문자열. 직전 값보다 항상 커서(마이크로초 단위) 같은 시각이 두 번 나오지 않는다.
    Windows 처럼 시계 해상도가 거친 곳에서 같은 세션의 두 턴 키가 겹쳐 앞의 턴을 덮어쓰는 일을 막는다."""
    global _last_ts
    with _clock_lock:
        now = datetime.now(timezone.utc)
        if _last_ts and now.isoformat(timespec="microseconds") <= _last_ts:
            now = datetime.fromisoformat(_last_ts) + timedelta(microseconds=1)
        _last_ts = now.isoformat(timespec="microseconds")
        return _last_ts


# 상담 대화로 인정하지 않는 턴의 요청 종류(backend.chat.Reply.action). 가드레일에 막힌 발화와 범위 밖 질문은 저장하지 않는다.
# backend/chat.py 가 모델 대화 기록(state.history)에 넣지 않는 턴과 같은 기준이다. 처리 오류(reply 없음)와 검사 실패(Reply.failed)도 저장하지 않는다.
UNSAVED_ACTIONS = ("blocked", "out_of_scope")


def enabled() -> bool:
    """대화 저장소가 준비됐는가. 모의 모드는 로컬 SQLite 라 늘 켜지고, 실제 모드는 DYNAMODB_CHAT_TABLE 이 있어야 켜진다.
    꺼져 있으면 저장하지 않으면서 '저장됩니다'라고 안내하는 일이 없도록 화면도 이 값을 따른다."""
    return is_mock() or bool(os.getenv("DYNAMODB_CHAT_TABLE", "").strip())


def mask(text: str) -> str:
    for pattern, label in _MASKS:
        text = pattern.sub(label, text)
    return text


def _title(prompt: str) -> str:
    one_line = " ".join(prompt.split())
    return one_line[:TITLE_CHARS] + ("…" if len(one_line) > TITLE_CHARS else "")


def _check(nickname: str | None, session_id: str) -> str | None:
    """Return the member key, or None for a guest. A malformed session id is a bug, not a guest."""
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("세션 ID 형식이 잘못되었습니다.")
    return (nickname or "").strip() or None


# ---------- 모의 모드: 로컬 SQLite ----------

def _db_path() -> Path:
    override = os.getenv("SKILLPATH_MOCK_CHAT_DB")
    if override:
        return Path(override)
    data_home = Path(os.getenv("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local" / "share"
    return data_home / "skillpath-ai" / "chat-history.sqlite3"


def _connect(create: bool) -> sqlite3.Connection | None:
    path = _db_path()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))  # 대화는 본인만 읽게 만든다
    elif not path.exists():
        return None
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sessions (nickname TEXT, session_id TEXT, title TEXT, updated_at TEXT, "
        "PRIMARY KEY (nickname, session_id))")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS turns (nickname TEXT, session_id TEXT, ts TEXT, prompt TEXT, answer TEXT, "
        "courses TEXT, PRIMARY KEY (nickname, session_id, ts))")
    return connection


# ---------- 실제 모드: DynamoDB ----------

def _table():
    name = os.getenv("DYNAMODB_CHAT_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_CHAT_TABLE을 설정하세요.")
    return _resource().Table(name)


def _query(table, nickname: str, prefix: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    items, start = [], {}
    while True:
        page = table.query(
            KeyConditionExpression=Key("nickname").eq(nickname) & Key("sk").begins_with(prefix),
            ConsistentRead=True, **start)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        start = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


# ---------- 공개 함수 ----------

def append_turn(nickname: str | None, session_id: str, prompt: str, answer: str,
                courses: list[dict] | tuple = ()) -> bool:
    """한 턴을 저장한다. 저장했으면 True, 비회원이거나 실패하면 False."""
    member = _check(nickname, session_id)
    if member is None or not enabled():
        return False
    prompt, answer = mask(prompt), mask(answer)
    now = _now()
    packed = json.dumps(list(courses), ensure_ascii=False, default=str)
    try:
        if is_mock():
            with closing(_connect(create=True)) as db, db:
                db.execute("INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?)",
                           (member, session_id, now, prompt, answer, packed))
                db.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(nickname, session_id) DO UPDATE SET updated_at = excluded.updated_at",
                    (member, session_id, _title(prompt), now))
        else:
            table = _table()
            days = int(os.getenv("CHAT_HISTORY_TTL_DAYS") or DEFAULT_TTL_DAYS)
            expires = int(time.time()) + days * 86400  # DynamoDB TTL 은 테이블에서 expires_at 으로 켠다
            # 턴을 먼저 쓰고 세션 줄을 나중에 쓴다. 둘 사이에서 실패해도 다음 턴이 세션 줄을 만들어 목록이 복구된다.
            table.put_item(Item={"nickname": member, "sk": f"T#{session_id}#{now}", "prompt": prompt,
                                 "answer": answer, "courses": packed, "expires_at": expires})
            table.update_item(
                Key={"nickname": member, "sk": f"S#{session_id}"},
                # 속성 이름은 별칭으로 쓴다(DynamoDB 예약어와 겹쳐도 요청이 거절되지 않는다)
                UpdateExpression="SET #title = if_not_exists(#title, :title), #updated = :now, #expires = :expires",
                ExpressionAttributeNames={"#title": "title", "#updated": "updated_at", "#expires": "expires_at"},
                ExpressionAttributeValues={":title": _title(prompt), ":now": now, ":expires": expires})
        return True
    except Exception as exc:  # 대화·닉네임이 예외 문구에 섞일 수 있어 종류만 남긴다
        logger.warning("대화 기록을 저장하지 못했습니다(%s).", type(exc).__name__)
        return False


def record_reply(nickname: str | None, session_id: str, prompt: str, reply) -> bool:
    """상담 답 한 건을 저장할지 정하고 저장한다. 화면은 이 함수를 부르기만 한다.

    reply 는 backend.chat.Reply 이거나 None(처리 오류). 막힌 발화·범위 밖·검사 실패·오류는 저장하지 않고 False 를 돌려준다.
    Reply 를 직접 가져오지 않고 속성으로만 읽는다(backend.chat 과 서로 불러오지 않게)."""
    if reply is None or getattr(reply, "failed", False) or getattr(reply, "action", "") in UNSAVED_ACTIONS:
        return False
    try:
        return append_turn(nickname, session_id, prompt, reply.text, getattr(reply, "courses", []))
    except ValueError:  # 세션 ID 형식 오류. append_turn 은 버그를 드러내려고 그대로 올리지만, 상담 화면이 깨지면 안 된다
        logger.warning("대화 기록을 저장하지 못했습니다(세션 ID 형식).")
        return False


def list_sessions(nickname: str | None) -> list[dict]:
    """회원의 세션 목록을 최근 순으로 돌려준다: [{session_id, title, updated_at}]. 비회원은 빈 목록."""
    member = (nickname or "").strip()
    if not member or not enabled():
        return []
    if is_mock():
        db = _connect(create=False)
        if db is None:
            return []
        with closing(db):
            rows = db.execute("SELECT session_id, title, updated_at FROM sessions WHERE nickname = ?", (member,))
            sessions = [dict(zip(("session_id", "title", "updated_at"), r)) for r in rows]
    else:
        sessions = [{"session_id": i["sk"][2:], "title": i["title"], "updated_at": i["updated_at"]}
                    for i in _query(_table(), member, "S#")]
    return sorted(sessions, key=lambda s: s["updated_at"], reverse=True)


def load_session(nickname: str | None, session_id: str) -> list[Turn]:
    """세션 하나의 대화를 시간 순으로 돌려준다. 비회원이거나 없는 세션이면 빈 목록."""
    member = _check(nickname, session_id)
    if member is None or not enabled():
        return []
    if is_mock():
        db = _connect(create=False)
        if db is None:
            return []
        with closing(db):
            rows = db.execute("SELECT prompt, answer, courses FROM turns WHERE nickname = ? AND session_id = ? "
                              "ORDER BY ts", (member, session_id)).fetchall()
    else:
        items = sorted(_query(_table(), member, f"T#{session_id}#"), key=lambda i: i["sk"])
        rows = [(i["prompt"], i["answer"], i["courses"]) for i in items]
    return [(prompt, answer, json.loads(courses)) for prompt, answer, courses in rows]
