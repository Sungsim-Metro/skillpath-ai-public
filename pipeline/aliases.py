"""기술 표현을 대표 이름으로 맞추는 별칭 사전. 정확히 일치하는 표현만 합친다.

- 대소문자, 공백, `-_·./()` 는 무시한다(CI/CD = CICD, k8s = K8S).
- 사전에 없으면 None 이다. 비슷해 보여도 합치지 않는다(Java ≠ JavaScript, EKS ≠ Kubernetes).
  없는 표현은 신규 후보로 모아 사람이 사전에 더한다.
- 사전은 data/aliases.json: {대표 이름: [별칭, ...]}. 대표 이름 자체도 별칭으로 취급한다.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

ALIAS_FILE = Path(__file__).resolve().parent.parent / "data" / "aliases.json"


def skill_aliases() -> dict[str, list[str]]:
    """공개 데모에 전체 사전이 없으면 배포된 표준 기술명과 기본 표기를 사용한다."""
    try:
        return json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if os.getenv("BEDROCK_MOCK", "1") != "1":
            raise
    areas = json.loads(ALIAS_FILE.with_name("skill_areas.json").read_text(encoding="utf-8"))
    demo = {"Docker": ["도커"], "Python": ["파이썬"], "Linux": ["리눅스"],
            "Kubernetes": ["쿠버네티스", "k8s"], "Git": ["깃"]}
    return {name: demo.get(name, []) for name in areas["skills"]}


def _key(s: str) -> str:
    return re.sub(r"[\s\-_·./()]", "", s).lower()


@lru_cache
def _index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canon, aliases in skill_aliases().items():
        for name in [canon, *aliases]:
            k = _key(name)
            if idx.setdefault(k, canon) != canon:  # 같은 표현이 두 대표명에 걸리면 사전 오류
                raise ValueError(f"별칭 충돌: '{name}' 이(가) {idx[k]} 와 {canon} 에 모두 있습니다")
    return idx


def normalize_skill(raw: str) -> str | None:
    """표현을 대표 이름으로. 사전에 없으면 None."""
    return _index().get(_key(raw))


@lru_cache(maxsize=1)
def _mention_pattern() -> re.Pattern[str]:
    names = {name for canon, aliases in skill_aliases().items() for name in (canon, *aliases)}
    variants = []
    for name in sorted(names, key=len, reverse=True):
        if name == "Go":
            # 영어 안내문의 'go' 및 'Go (directly) to ...'와 프로그래밍 언어를 구분한다.
            variants.append(r"Go(?![ \t]+(?:[a-z]+ly[ \t]+)?to\b)")
        else:
            parts = re.split(r"[\s\-_·./]+", name)
            spelling = r"[ \t_·./-]*".join(re.escape(part) for part in parts)
            variants.append(f"(?ai:{spelling})")

    # 오른쪽에는 조사만 붙을 수 있다. '자바는'은 Java이고 '자바스크립트'는 별개 낱말이다.
    particle = r"(?:으로|로|에서|에게|처럼|보다|부터|까지|에|과|와|의|은|는|이|가|을|를|도|만)(?:는|도|만|의)?"
    return re.compile(r"(?<!\w)(?:" + "|".join(variants) + rf")(?=(?:{particle})?(?!\w))")


def find_skills(text: str) -> list[str]:
    """본문에 등장한 공통 사전의 기술을 반환한다. 겹치는 표기는 긴 별칭을 먼저 취한다."""
    index = _index()
    return sorted({index[_key(match.group())] for match in _mention_pattern().finditer(text)})


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
