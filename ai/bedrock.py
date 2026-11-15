"""Bedrock Converse 호출과 JSON 응답 처리.

- BEDROCK_MOCK=1 이면 AWS 를 부르지 않고 data/sample/ 의 가짜 응답을 돌려준다.
- 응답은 JSON 객체로 받는다. 파싱이나 형식 검사에 실패하면 한 번만 다시 묻고,
  그래도 실패하면 BedrockJSONError 를 그대로 낸다.
- 인증은 boto3 기본 방식(AWS_PROFILE 등)을 따른다. 키 값은 코드와 로그에 남기지 않는다.
- 호출마다 usage(토큰 수)를 로그로 남기고 결과에도 담는다. 실패한 호출의 토큰도 오류에 담아 잃지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable, NamedTuple, Optional

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
DEFAULT_REGION = "ap-northeast-2"
USAGE_KEYS = ("inputTokens", "outputTokens", "totalTokens")
RETRY_MESSAGE = (
    "직전 응답이 형식 검사를 통과하지 못했습니다 ({error}). "
    "설명 없이 형식에 맞는 JSON 객체 하나만 다시 출력하세요."
)

logger = logging.getLogger(__name__)


class BedrockJSONError(RuntimeError):
    """두 번 모두 JSON 파싱이나 형식 검사(validate)를 통과하지 못했을 때.

    usage 에는 실패한 두 번의 호출에 쓴 토큰 합계가 들어 있다.
    """

    def __init__(self, message: str, usage: Optional[dict] = None):
        super().__init__(message)
        self.usage = usage or {k: 0 for k in USAGE_KEYS}


class ConverseResult(NamedTuple):
    data: dict
    attempts: int  # 1 이면 한 번에 성공, 2 면 재시도 후 성공
    usage: dict    # 시도 전체의 inputTokens, outputTokens, totalTokens 합 (가짜 모드는 0)
    calls: tuple = ()  # 시도마다 {"attempt", "inputTokens", "outputTokens", "totalTokens"}


def add_usage(total: dict, usage: dict) -> dict:
    """usage 합산 도우미. 대화 1건의 여러 호출을 더할 때 쓴다 (#25)."""
    for k in USAGE_KEYS:
        total[k] = total.get(k, 0) + usage.get(k, 0)
    return total


def is_mock() -> bool:
    return os.getenv("BEDROCK_MOCK", "1") == "1"


def parse_json_object(text: str) -> dict:
    """응답 문자열을 JSON 객체로 읽는다. ```json 울타리만 벗기고 그 밖의 보정은 하지 않는다."""
    s = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, flags=re.DOTALL)
    if fence:
        s = fence.group(1)
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return data


def converse_json(
    system: str,
    user_text: str,
    *,
    mock_file: str,
    mock_key: str,
    validate: Optional[Callable[[dict], None]] = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> ConverseResult:
    """한 번 묻고 JSON 으로 받는다. validate 는 형식이 틀리면 ValueError 를 내는 함수."""
    messages = [{"role": "user", "content": [{"text": user_text}]}]
    usage = {k: 0 for k in USAGE_KEYS}
    calls = []
    last_error = ""
    text = ""
    for attempt in (1, 2):
        text, stop_reason, call_usage = _call(
            system, messages, max_tokens, temperature, mock_file, mock_key, attempt
        )
        add_usage(usage, call_usage)
        calls.append({"attempt": attempt, **{k: call_usage.get(k, 0) for k in USAGE_KEYS}})
        logger.info(
            "Bedrock 호출 key=%s 시도=%d 입력=%d 출력=%d 토큰 (stopReason=%s)",
            mock_key, attempt, call_usage.get("inputTokens", 0), call_usage.get("outputTokens", 0), stop_reason,
        )
        try:
            data = parse_json_object(text)
            if validate:
                validate(data)
            return ConverseResult(data, attempt, usage, tuple(calls))
        except ValueError as e:  # json.JSONDecodeError 도 ValueError
            last_error = f"{e} (stopReason={stop_reason})"
            messages += [
                {"role": "assistant", "content": [{"text": text or "(빈 응답)"}]},
                {"role": "user", "content": [{"text": RETRY_MESSAGE.format(error=e)}]},
            ]
    raise BedrockJSONError(
        f"응답 검사 2회 실패 (JSON 파싱 또는 형식 검사): {last_error} / 마지막 응답 앞부분: {text[:300]!r}",
        usage,
    )


def _call(system, messages, max_tokens, temperature, mock_file, mock_key, attempt):
    """(응답 문자열, stopReason, usage) 를 돌려준다."""
    if is_mock():
        return _mock_text(mock_file, mock_key, attempt), "mock", {}
    resp = _client().converse(
        modelId=_model_id(),
        system=[{"text": system}],
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    blocks = resp["output"]["message"]["content"]
    text = "".join(b["text"] for b in blocks if "text" in b)
    return text, resp.get("stopReason"), resp.get("usage", {})


@lru_cache(maxsize=1)
def _client():
    import boto3  # 가짜 모드에서는 불러오지 않는다

    return boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", DEFAULT_REGION))


def _model_id() -> str:
    model_id = os.getenv("BEDROCK_MODEL_ID")
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID 가 비어 있습니다. .env 에 모델 ID 를 적어 주세요.")
    return model_id


def _mock_text(mock_file: str, key: str, attempt: int) -> str:
    """가짜 응답. 키가 없으면 default. 목록이면 n번째 시도에 n번째 응답을 쓴다."""
    table = json.loads((SAMPLE_DIR / mock_file).read_text(encoding="utf-8"))
    entry = table.get(key, table["default"])
    if isinstance(entry, list):
        entry = entry[min(attempt, len(entry)) - 1]
    return entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
