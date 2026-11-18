"""Bedrock 호출 기록 (토큰·비용 화면용, 2026-11-16 결정).

모델 응답마다 record()가 토큰을 합산하고 JSON 한 줄을 덧붙인다.
- 남기는 것: 시각, 단계, 시도 번호, 입력·출력 토큰 수, 모델 ID, 가짜 모드 여부. 상담 중인 호출에는 익명 세션 ID도 남긴다
- 남기지 않는 것: 프롬프트, 응답, 사용자 입력, 닉네임. 내용은 한 글자도 적지 않는다
- 시각은 서버 시간대와 상관없이 한국 시간으로 적는다(날짜별 집계가 한국 날짜와 맞게)
- 파일은 USAGE_LOG(기본 outputs/usage/usage.jsonl, 커밋되지 않음). USAGE_LOG=off 면 기록하지 않는다(테스트용). 서버 한 대의 로컬 파일이라 서버를 바꾸면 이어지지 않는다
- 기록에 실패해도 호출은 그대로 진행한다(화면이 기록 때문에 멈추지 않게)
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "outputs" / "usage" / "usage.jsonl"
_lock = Lock()
KST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)


@dataclass
class SessionUsage:
    """Token totals for real model responses in one chat session."""

    session_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_session: ContextVar[SessionUsage | None] = ContextVar("usage_session", default=None)


@contextmanager
def track_session(usage: SessionUsage) -> Iterator[None]:
    """Attribute calls within this scope to the supplied session."""
    previous = _session.set(usage)
    try:
        yield
    finally:
        _session.reset(previous)


def log_path() -> Path:
    return Path(os.getenv("USAGE_LOG") or DEFAULT_LOG)


def stage_of(mock_file: str) -> str:
    """가짜 응답 파일 이름에서 단계 이름을 얻는다. mock_bedrock_recommend.json → recommend"""
    stem = Path(mock_file).stem
    return stem.removeprefix("mock_bedrock_") or "unknown"


def record(
    mock_file: str, attempt: int, usage: dict, mock: bool, *, model_id: str | None = None
) -> None:
    """Account for a model response and append its metadata to the usage log."""
    session = _session.get()
    logging_enabled = os.getenv("USAGE_LOG") != "off"
    if session is None and not logging_enabled:
        return
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        if session is not None and not mock:
            with _lock:
                session.input_tokens += input_tokens
                session.output_tokens += output_tokens
        if not logging_enabled:
            return
        row = {
            "ts": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage_of(mock_file),
            "attempt": attempt,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "model": "mock" if mock else model_id or os.getenv("BEDROCK_MODEL_ID", ""),
            "mock": mock,
        }
        if session is not None:
            row["session_id"] = session.session_id
        path = log_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError, AttributeError) as e:  # 파일을 못 쓰거나 usage 모양이 예상과 달라도 호출은 막지 않는다
        logger.warning("호출 기록을 쓰지 못했습니다(%s). 호출은 계속합니다", type(e).__name__)
