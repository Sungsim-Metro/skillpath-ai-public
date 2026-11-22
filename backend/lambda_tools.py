"""Invoke the chat tools synchronously and expose their private Lambda entry points."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from functools import lru_cache
from time import monotonic
from uuid import UUID, uuid4

from ai import bedrock, usage
from backend.jobs import JOBS

DEMAND_FUNCTION = "SKILLPATH_DEMAND_FUNCTION"
RECOMMEND_FUNCTION = "SKILLPATH_RECOMMEND_FUNCTION"
_cache_hits: ContextVar[set[str] | None] = ContextVar("lambda_tool_cache_hits", default=None)
_turn_id: ContextVar[str | None] = ContextVar("lambda_tool_turn_id", default=None)


@contextmanager
def track_turn(turn_id: str) -> Iterator[None]:
    """Correlate all remote calls for this turn, including the comparison arm."""
    previous = _turn_id.set(turn_id)
    try:
        yield
    finally:
        _turn_id.reset(previous)


def mark_cache_hit(cache: str) -> None:
    hits = _cache_hits.get()
    if hits is not None:
        hits.add(cache)


def configured_function(variable: str) -> str:
    """Opt into routing only in the live app. Nested tool calls run locally in Lambda."""
    if _cache_hits.get() is not None or bedrock.is_mock():
        return ""
    return os.getenv(variable, "").strip()


@lru_cache(maxsize=4)
def _client(region: str, read_timeout: int):
    import boto3
    from botocore.config import Config

    # An ambiguous transport timeout can follow a completed model invocation.
    config = Config(connect_timeout=5, read_timeout=read_timeout, retries={"total_max_attempts": 1})
    return boto3.client("lambda", region_name=region, config=config)


def invoke(function: str, operation: str, **arguments) -> dict | list[dict]:
    """Execute a tool once and account for every reported model call, including failed reviews."""
    request = {"operation": operation, "arguments": arguments}
    if turn_id := _turn_id.get():
        request["turn_id"] = turn_id
    read_timeout = 45  # The demand function runs for at most 30 seconds.
    if operation in ("get_recommendations", "recommend_with"):
        request["model_id"] = bedrock.current_model()
        read_timeout = 910  # Allow the configured recommendation timeout up to Lambda's 900-second maximum.
    response = _client(os.getenv("AWS_REGION", "ap-northeast-2"), read_timeout).invoke(
        FunctionName=function, InvocationType="RequestResponse",
        Payload=json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8"),
    )
    with closing(response["Payload"]) as payload:
        if response.get("FunctionError") or response.get("StatusCode") != 200:
            raise RuntimeError("The Lambda tool failed to execute.")
        reply = json.loads(payload.read())
    for row in reply["usage"]:
        usage.record(row["stage"], row["attempt"], row, row["mock"], model_id=row["model"], timestamp=row["ts"])
    if error := reply.get("error"):
        if error == "validation":
            raise ValueError("The Lambda tool could not validate the request or its result.")
        if error == "missing":
            raise LookupError("The requested tool data is unavailable.")
        raise RuntimeError("The Lambda tool could not complete the operation.")
    return reply["result"]


def _uuid(value) -> str | None:
    if isinstance(value, str) and len(value) in (32, 36):
        try:
            parsed = UUID(value)
        except ValueError:
            return None
        if parsed.version == 4:
            return parsed.hex
    return None


def _handle(event: dict, operations: dict, context) -> dict:
    started = monotonic()
    event = event if isinstance(event, dict) else {}
    operation = event.get("operation")
    known = isinstance(operation, str) and operation in operations
    hits: set[str] = set()
    token = _cache_hits.set(hits)
    try:
        with usage.collect_remote_usage() as rows:
            try:
                if not known:
                    raise ValueError("Unrecognized tool operation.")
                arguments = dict(event["arguments"])
                if arguments.get("job_category") not in JOBS:
                    raise ValueError("Unsupported job category.")
                if arguments.get("course_levels") is not None:
                    arguments["course_levels"] = tuple(arguments["course_levels"])
                with bedrock.use_model(event.get("model_id")):
                    result = operations[operation](**arguments)
                response = {"result": result, "usage": rows}
            except (bedrock.BedrockJSONError, KeyError, TypeError, ValueError):
                response = {"error": "validation", "usage": rows}
            except LookupError:
                response = {"error": "missing", "usage": rows}
            except Exception:
                response = {"error": "unavailable", "usage": rows}
    finally:
        _cache_hits.reset(token)
    request_id = _uuid(getattr(context, "aws_request_id", None)) or uuid4().hex
    measured = [row for row in rows if not row["mock"]]
    # Lambda captures this single JSON line in CloudWatch even at the default WARN log level.
    print(json.dumps({
        "event": "skillpath_tool", "operation": operation if known else "unknown",
        "request_id": request_id, "turn_id": _uuid(event.get("turn_id")) or request_id,
        "duration_ms": round((monotonic() - started) * 1000), "success": "error" not in response,
        "error": response.get("error"), "cache_hits": sorted(hits), "model_responses": len(measured),
        "input_tokens": sum(row["inputTokens"] for row in measured),
        "output_tokens": sum(row["outputTokens"] for row in measured),
    }))
    return response


def demand_handler(event: dict, context) -> dict:
    """Serve the read-only demand and personal gap tools."""
    from backend import api

    return _handle(event, {"get_demand": api.get_demand, "get_gap": api.get_gap}, context)


def recommend_handler(event: dict, context) -> dict:
    """Serve standard recommendations and both experimental recommendation structures."""
    from backend import api, experiments

    return _handle(event, {"get_recommendations": api.get_recommendations,
                          "recommend_with": experiments.recommend_with}, context)
