"""Bedrock Converse 호출과 JSON 응답 처리.

- BEDROCK_MOCK=1 이면 AWS 를 부르지 않고 data/sample/ 의 가짜 응답을 돌려준다.
- 응답은 JSON 객체로 받는다. 파싱이나 형식 검사에 실패하면 한 번만 다시 묻고,
  그래도 실패하면 BedrockJSONError 를 그대로 낸다.
- 인증은 boto3 기본 방식(AWS_PROFILE 등)을 따른다. 키 값은 코드와 로그에 남기지 않는다.
- 호출마다 usage(토큰 수)를 로그로 남기고 결과에도 담는다. 실패한 호출의 토큰도 오류에 담아 잃지 않는다.
- converse_tools: 상담 에이전트(#25)용 도구 호출 반복. JSON 검사는 부르는 쪽(ai/agent.py)이 한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from ai.usage import record as record_usage

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
DEFAULT_REGION = "ap-northeast-2"
USAGE_KEYS = ("inputTokens", "outputTokens", "totalTokens")
RETRY_MESSAGE = (
    "직전 응답이 형식 검사를 통과하지 못했습니다 ({error}). "
    "설명 없이 형식에 맞는 JSON 객체 하나만 다시 출력하세요."
)

logger = logging.getLogger(__name__)
# 이 범위 안의 호출에만 쓰는 모델 ID. 없으면 BEDROCK_MODEL_ID 를 쓴다(상담 실험의 모델 무작위 배정, 2026-11-19 회의)
_model_override: ContextVar[Optional[str]] = ContextVar("bedrock_model", default=None)


@contextmanager
def use_model(model_id: Optional[str]):
    """이 범위 안의 Bedrock 호출을 model_id 로 보낸다. 빈 값이면 아무것도 바꾸지 않는다."""
    token = _model_override.set(model_id or None)
    try:
        yield
    finally:
        _model_override.reset(token)


def current_model() -> str:
    """지금 호출에 쓰일 모델 ID(없으면 빈 문자열). 가짜 모드 여부는 보지 않는다."""
    return _model_override.get() or os.getenv("BEDROCK_MODEL_ID", "")


class BedrockJSONError(RuntimeError):
    """허용한 시도에서 JSON 파싱이나 형식 검사(validate)를 통과하지 못했을 때.

    usage 에는 실패한 호출에 쓴 토큰 합계가 들어 있다.
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
    """응답 문자열을 JSON 객체로 읽는다. ```json 울타리만 벗기고 그 밖의 보정은 하지 않는다.

    울타리 앞뒤에 설명 글이 붙어 있어도 울타리 안의 객체를 읽는다(2026-11-20: 실제 모델이 추천 이유 수정 응답에
    설명을 덧붙여 재시도 없는 호출이 그대로 실패했다). 울타리가 없으면 전체가 JSON 이어야 한다. 값은 고치지 않는다.
    """
    s = text.strip()
    try:
        data = json.loads(s)
    except ValueError:
        fence = re.search(r"```(?:json)?\s*(\{.*?)\s*```", s, flags=re.DOTALL)
        if not fence:
            raise
        data = json.loads(fence.group(1))
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
    max_attempts: int = 2,
    usage_stage: Optional[str] = None,
) -> ConverseResult:
    """JSON 응답을 검사한다. 기본은 1회 재시도이며 1을 지정하면 재시도하지 않는다."""
    if type(max_attempts) is not int or max_attempts not in (1, 2):
        raise ValueError("max_attempts는 1 또는 2여야 합니다.")
    messages = [{"role": "user", "content": [{"text": user_text}]}]
    usage = {k: 0 for k in USAGE_KEYS}
    calls = []
    last_error = ""
    text = ""
    for attempt in range(1, max_attempts + 1):
        text, stop_reason, call_usage = _call(
            system, messages, max_tokens, temperature, mock_file, mock_key, attempt
        )
        add_usage(usage, call_usage)
        record_usage(usage_stage or mock_file, attempt, call_usage, is_mock(), model_id=current_model() or None)
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
        f"응답 검사 {max_attempts}회 실패 (JSON 파싱 또는 형식 검사): "
        f"{last_error} / 마지막 응답 앞부분: {text[:300]!r}",
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
    model_id = current_model()
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID 가 비어 있습니다. .env 에 모델 ID 를 적어 주세요.")
    return model_id


class ToolLoopResult(NamedTuple):
    """converse_tools 결과. text 는 마지막 응답의 글(도구를 더 부르지 않은 응답). 반복 한도에 걸리면 빈 문자열."""
    text: str
    messages: list      # 대화 전체(도구 호출·결과 포함). 이어서 물을 때 그대로 넘긴다
    tool_calls: tuple   # 도구 호출마다 {"name", "input", "ok"} (final_tool 은 넣지 않는다)
    model_calls: int    # 모델을 부른 횟수
    usage: dict         # 모델 호출 전체의 토큰 합 (가짜 모드는 0)
    stop: str           # "end_turn"(글로 끝남), "final_tool"(마지막 답 도구로 끝남), "max_turns"(한도까지 도구만 부름)
    final: Optional[dict] = None  # stop 이 final_tool 이면 {"id": toolUseId, "input": 도구 입력}. 이어서 물을 때 이 id 로 toolResult 를 준다


FINAL_TOO_EARLY = "다른 도구 결과가 아직 없어 이 답은 받지 않았습니다. 도구 결과를 본 뒤 answer 만 다시 부르세요."
FINAL_TWICE = "answer 가 한 차례에 두 번 이상 불려 어느 답도 받지 않았습니다. answer 를 한 번만 다시 부르세요."


def converse_tools(
    system: str,
    messages: list,
    tools: list[dict],
    run_tool: Callable[[str, dict], dict],
    *,
    mock_file: str,
    mock_key: str,
    max_turns: int = 4,
    mock_turn_start: int = 1,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    final_tool: Optional[str] = None,
    attempt: int = 1,
) -> ToolLoopResult:
    """Converse 도구 호출 반복 (#25). 모델이 도구를 고르면 run_tool 로 실행해 결과를 돌려주고, 글로 답하면 멈춘다.

    tools 는 [{"name", "description", "inputSchema": {"json": JSON Schema}}] (Converse toolSpec 형식).
    run_tool(name, input) 은 JSON 으로 바꿀 수 있는 dict 를 돌려준다. 예외가 나면 status "error" 로 모델에 알린다.
    final_tool: 마지막 답을 받는 도구 이름. 이 도구만 불리면 실행하지 않고 그 입력을 final 로 돌려주며 멈춘다.
      같은 턴에 다른 도구와 같이 불리면 다른 도구를 먼저 실행하고, 마지막 답에는 FINAL_TOO_EARLY 오류를 돌려줘 다시 묻는다
      (결과를 보기 전에 쓴 답은 그 결과를 근거로 할 수 없다). 한 차례에 두 번 이상 불리면 모두 FINAL_TWICE 오류로 돌려준다.
    messages 는 바꾸지 않고 복사해서 쓴다. 가짜 모드는 mock_file[mock_key] 의 n번째 응답을 n번째 호출에 쓴다.
    """
    messages = list(messages)
    usage = {k: 0 for k in USAGE_KEYS}
    calls: list[dict] = []
    for turn in range(max_turns):
        content, stop_reason, call_usage = _call_tools(
            system, messages, tools, max_tokens, temperature, mock_file, mock_key, mock_turn_start + turn
        )
        add_usage(usage, call_usage)
        record_usage(mock_file, attempt, call_usage, is_mock(), model_id=current_model() or None)
        logger.info(
            "Bedrock 도구 호출 key=%s 회차=%d 입력=%d 출력=%d 토큰 (stopReason=%s)",
            mock_key, turn + 1, call_usage.get("inputTokens", 0), call_usage.get("outputTokens", 0), stop_reason,
        )
        messages.append({"role": "assistant", "content": content})
        uses = [b["toolUse"] for b in content if "toolUse" in b]
        text = "".join(b["text"] for b in content if "text" in b)
        if not uses:
            return ToolLoopResult(text, messages, tuple(calls), turn + 1, usage, "end_turn")
        finals = [u for u in uses if u["name"] == final_tool]
        if len(finals) == 1 and len(uses) == 1:  # 마지막 답은 한 차례에 그것 하나만 불렸을 때만 받는다
            final = finals[0]
            return ToolLoopResult(text, messages, tuple(calls), turn + 1, usage, "final_tool",
                                  {"id": final["toolUseId"], "input": final.get("input") or {}})
        early = FINAL_TWICE if len(finals) > 1 else FINAL_TOO_EARLY
        results = []
        for use in uses:  # Converse 규칙: 직전 응답의 toolUse 하나하나에 toolResult 를 돌려준다
            if use["name"] == final_tool:  # 다른 도구와 같이, 또는 두 번 이상 불린 마지막 답: 받지 않고 다시 묻는다
                results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": [{"text": early}],
                                               "status": "error"}})
                continue
            try:
                out, status = [{"json": run_tool(use["name"], use.get("input") or {})}], "success"
            except Exception as e:  # noqa: BLE001 - 도구 실패는 모델에게 알리고 대화는 이어 간다
                logger.warning("도구 %s 실패: %s", use["name"], type(e).__name__)
                out, status = [{"text": f"도구 실행 실패: {type(e).__name__}"}], "error"
            calls.append({"name": use["name"], "input": use.get("input") or {}, "ok": status == "success"})
            results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": out, "status": status}})
        messages.append({"role": "user", "content": results})
    return ToolLoopResult("", messages, tuple(calls), max_turns, usage, "max_turns")


def _call_tools(system, messages, tools, max_tokens, temperature, mock_file, mock_key, turn):
    """(응답 content 블록 목록, stopReason, usage)."""
    if is_mock():
        return _mock_turn(mock_file, mock_key, turn), "mock", {}
    resp = _client().converse(
        modelId=_model_id(),
        system=[{"text": system}],
        messages=messages,
        toolConfig={"tools": [{"toolSpec": t} for t in tools]},
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return resp["output"]["message"]["content"], resp.get("stopReason"), resp.get("usage", {})


def _mock_turn(mock_file: str, key: str, turn: int) -> list:
    """도구 호출 가짜 응답: mock_file[key] 의 turn 번째 content 블록 목록. 모자라면 마지막 것을 쓴다."""
    table = json.loads((SAMPLE_DIR / mock_file).read_text(encoding="utf-8"))
    turns = table.get(key, table["default"])
    return json.loads(json.dumps(turns[min(turn, len(turns)) - 1]))  # 호출마다 새 객체


def _mock_text(mock_file: str, key: str, attempt: int) -> str:
    """가짜 응답. 키가 없으면 default. 목록이면 n번째 시도에 n번째 응답을 쓴다."""
    table = json.loads((SAMPLE_DIR / mock_file).read_text(encoding="utf-8"))
    entry = table.get(key, table["default"])
    if isinstance(entry, list):
        entry = entry[min(attempt, len(entry)) - 1]
    return entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
