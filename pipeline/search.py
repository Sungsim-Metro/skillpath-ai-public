"""기술 → 교육과정 후보 (#14). 임베딩 없이 저장소에 있는 연결표만 쓴다.

1. 기술 → 세부 능력: pipeline/courses/queries.csv 의 aliases 에 기술 이름이 '그대로' 있는 세부 능력.
   (부분 일치는 쓰지 않는다. 'AWS' 가 'AWS CLI' 에 걸리면 무엇이든 추천하게 된다.)
2. 세부 능력 → 과정: data/courses/course_subskill.csv. certain 먼저, 과정 설명에 그 기술 이름이 나오는 과정 먼저,
   그다음 검색 순위(search_rank) 순.
- 맞는 세부 능력이 없으면 빈 목록이다(AWS·Azure·GCP 같은 포괄어, 목록에 없는 기술). 그럴듯하게 채우지 않는다.
- 반환은 [{"course_id", "subskill_id"}]. 제목·링크·난이도·match 는 부르는 쪽(ai/recommend.py)이 CSV 에서 채운다.
- 임베딩이나 Knowledge Base 로 바꿀 때도 이 함수 안쪽만 바꾼다.
"""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path

from pipeline.aliases import ALIAS_FILE, _key, normalize_skill

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "pipeline" / "courses" / "queries.csv"
COURSES = ROOT / "data" / "courses" / "courses.csv"
LINKS = ROOT / "data" / "courses" / "course_subskill.csv"


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def _subskills_by_alias() -> dict[str, list[str]]:
    """정규화한 별칭 → 세부 능력 id 목록."""
    out: dict[str, list[str]] = {}
    for row in _read(QUERIES):
        for alias in row["aliases"].split("|"):
            if alias.strip():
                out.setdefault(_key(alias), []).append(row["subskill_id"])
    return out


@lru_cache(maxsize=1)
def _links_by_subskill() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in _read(LINKS):
        out.setdefault(row["subskill_id"], []).append(row)
    return out


@lru_cache(maxsize=1)
def _course_text() -> dict[str, str]:
    return {r["course_id"]: " ".join([r["title"], r["description"], r["objectives"]]) for r in _read(COURSES)}


def _names(skill: str) -> list[str]:
    """대표 이름과 별칭. 사전에 없는 기술이면 입력 그대로."""
    aliases = json.loads(ALIAS_FILE.read_text(encoding="utf-8")).get(skill, [])
    return [skill, *aliases]


def _mentions(text: str, names: list[str]) -> bool:
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", text, re.I) for n in names if n.isascii()
    )


def subskills_of(skill: str) -> list[str]:
    """기술(대표명이나 별칭)이 속한 세부 능력 id 목록. 없으면 빈 목록."""
    names = _names(normalize_skill(skill) or skill)
    out: list[str] = []
    for name in names:
        for sid in _subskills_by_alias().get(_key(name), []):
            if sid not in out:
                out.append(sid)
    return out


def search_courses(skill: str, k: int = 3) -> list[dict]:
    """기술 이름(대표명이나 별칭)으로 과정 후보 최대 k개. 없으면 빈 목록."""
    names = _names(normalize_skill(skill) or skill)
    subskills = subskills_of(skill)
    texts = _course_text()
    rows = [r for sid in subskills for r in _links_by_subskill().get(sid, [])]
    rows.sort(key=lambda r: (
        r["match"] != "certain",
        not _mentions(texts.get(r["course_id"], ""), names),
        int(r["search_rank"] or 99),
    ))
    out, seen = [], set()
    for r in rows:
        if r["course_id"] in seen:
            continue
        seen.add(r["course_id"])
        out.append({"course_id": r["course_id"], "subskill_id": r["subskill_id"]})
        if len(out) == k:
            break
    return out
