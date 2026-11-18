"""Titan 임베딩 호출 (#13, #14).

- 모델 ID 는 코드에 적지 않고 .env 의 EMBED_MODEL_ID 로 읽는다. 리전은 AWS_REGION(기본 ap-northeast-2).
- BEDROCK_MOCK=1(기본)이면 AWS 를 부르지 않고 글자 두 개씩(bigram) 해시한 가짜 벡터를 돌려준다.
  비슷한 글자가 많은 문장이 비슷하게 나오므로 화면과 테스트에서 동작 확인용으로 쓸 수 있다. 점수에는 의미가 없다.
- 실제 호출 결과는 data/raw/embed_cache.json 에 저장해 같은 문장을 다시 부르지 않는다(저장소에 올리지 않는다).
- 호출마다 토큰 수를 usage() 에 더한다. 실패는 숨기지 않고 boto3 오류를 그대로 낸다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path

from ai.usage import record as record_usage
from pipeline.rocketpunch import load_env_file

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "raw" / "embed_cache.json"
MOCK_DIM = 256
_usage = {"calls": 0, "tokens": 0}


@lru_cache(maxsize=1)
def _setup() -> None:
    load_env_file()


def is_mock() -> bool:
    _setup()
    return os.getenv("BEDROCK_MOCK", "1") == "1"


def usage() -> dict:
    """지금까지 실제로 부른 횟수와 입력 토큰 수(가짜 모드는 0)."""
    return dict(_usage)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _mock_vector(text: str) -> list[float]:
    t = re.sub(r"\s+", "", text.lower())
    v = [0.0] * MOCK_DIM
    for i in range(max(len(t) - 1, 1)):
        v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % MOCK_DIM] += 1.0
    return _unit(v)


@lru_cache(maxsize=1)
def _client():
    import boto3  # 가짜 모드에서는 불러오지 않는다

    return boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "ap-northeast-2"))


def _model_id() -> str:
    model_id = os.getenv("EMBED_MODEL_ID")
    if not model_id:
        raise RuntimeError("EMBED_MODEL_ID 가 비어 있습니다. .env 에 모델 ID 를 적어 주세요.")
    return model_id


def _titan(text: str) -> list[float]:
    model_id = _model_id()
    res = _client().invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(res["body"].read())
    _usage["calls"] += 1
    input_tokens = body.get("inputTextTokenCount", 0)
    _usage["tokens"] += input_tokens
    record_usage("embedding", 1, {"inputTokens": input_tokens}, mock=False, model_id=model_id)
    return body["embedding"]


def embed_many(texts: list[str]) -> list[list[float]]:
    """문장마다 벡터(길이 1 로 맞춘 것). 실제 모드는 캐시에 없는 문장만 부른다."""
    if is_mock():
        return [_mock_vector(t) for t in texts]
    model = _model_id()
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    out, dirty = [], False
    for t in texts:
        key = f"{model}:{hashlib.sha1(t.encode('utf-8')).hexdigest()}"
        if key not in cache:
            cache[key] = [round(x, 6) for x in _titan(t)]
            dirty = True
        out.append(cache[key])
    if dirty:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    return out


def embed(text: str) -> list[float]:
    return embed_many([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    """코사인 유사도. 계산은 코드가 한다."""
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
