"""기술 표현을 대표 이름으로 맞추는 별칭 사전. 정확히 일치하는 표현만 합친다.

- 대소문자, 공백, `-_·./()` 는 무시한다(CI/CD = CICD, k8s = K8S).
- 사전에 없으면 None 이다. 비슷해 보여도 합치지 않는다(Java ≠ JavaScript, EKS ≠ Kubernetes).
  없는 표현은 신규 후보로 모아 사람이 사전에 더한다.
- 사전은 data/aliases.json: {대표 이름: [별칭, ...]}. 대표 이름 자체도 별칭으로 취급한다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ALIAS_FILE = Path(__file__).resolve().parent.parent / "data" / "aliases.json"


def _key(s: str) -> str:
    return re.sub(r"[\s\-_·./()]", "", s).lower()


@lru_cache
def _index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canon, aliases in json.loads(ALIAS_FILE.read_text(encoding="utf-8")).items():
        for name in [canon, *aliases]:
            k = _key(name)
            if idx.setdefault(k, canon) != canon:  # 같은 표현이 두 대표명에 걸리면 사전 오류
                raise ValueError(f"별칭 충돌: '{name}' 이(가) {idx[k]} 와 {canon} 에 모두 있습니다")
    return idx


def normalize_skill(raw: str) -> str | None:
    """표현을 대표 이름으로. 사전에 없으면 None."""
    return _index().get(_key(raw))


def normalize_skills(raws: list[str]) -> tuple[list[str], list[str]]:
    """(대표 이름 목록, 사전에 없는 표현 목록). 순서는 처음 나온 순서, 중복은 한 번만."""
    known: list[str] = []
    unknown: list[str] = []
    for raw in raws:
        canon = normalize_skill(raw)
        bucket, value = (known, canon) if canon else (unknown, raw)
        if value not in bucket:
            bucket.append(value)
    return known, unknown
