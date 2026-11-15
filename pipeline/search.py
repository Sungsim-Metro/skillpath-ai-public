"""기술 → 교육과정 후보 (#14). 임베딩 없이 저장소에 있는 연결표와 과정 본문만 쓴다.

순서
1. 기술 → 세부 능력: pipeline/courses/queries.csv 의 aliases 에 기술 이름이 '그대로' 있는 세부 능력.
   (부분 일치는 쓰지 않는다. 'AWS' 가 'AWS CLI' 에 걸리면 무엇이든 추천하게 된다.)
2. 이름이 과정의 제목·학습 목표에 나오는 과정을 먼저 준다(match = certain). 기초 → 중급 순, 제목에 나오는 과정 먼저.
   세부 능력만 같은 과정(예: 컨테이너 기초)이 Kubernetes 에 certain 으로 나오던 문제를 막는 단계다.
   설명(description)에만 나오는 것은 약해서 세웠다고 보지 않는다.
3. 자리가 남으면 같은 세부 능력으로 찾은 과정으로 채운다(match = uncertain). "관련 기초"라는 뜻이다.
- 맞는 세부 능력이 없으면 빈 목록이다(Azure·GCP, 목록에 없는 기술). 그럴듯하게 채우지 않는다.
- 단, AWS 처럼 넓어서 세부 능력으로 고를 수 없는 기술은 사람이 지정한 입문 과정(data/foundation_courses.json)을 준다.
  이때 subskill_id 는 None, match 는 uncertain 이다.
- 종료 예정이거나 학습 목표가 없는 과정은 뺀다(추천 이유를 쓸 근거가 없다).
- 반환은 [{"course_id", "subskill_id", "match"}]. 제목·링크·난이도는 부르는 쪽(ai/recommend.py)이 CSV 에서 채운다.
  match 는 이 함수가 정한 값이다. course_subskill.csv 의 match 는 "그 과정이 세부 능력을 다룬다"는 증거이지
  "그 과정이 이 기술을 다룬다"는 증거가 아니므로 그대로 쓰지 않는다.
  주의: subskill_id 가 None 인 항목이 있다. ai/recommend.py 의 candidate_courses 는 (course_id, subskill_id) 행이 없으면
  KeyError 를 내고 match 도 CSV 값만 쓰므로, search_courses 로 교체할 때 이 함수가 준 match 를 쓰고
  sid 가 None 이면 행 조회를 건너뛰는 분기를 함께 넣어야 한다(아직 없음).
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
LEVEL_ORDER = {"기초": 0, "중급": 1}  # 신입 대상이라 기초부터


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
def _links_by_course() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in _read(LINKS):
        out.setdefault(row["course_id"], []).append(row)
    return out


@lru_cache(maxsize=1)
def _courses() -> dict[str, dict]:
    return {r["course_id"]: r for r in _read(COURSES)}


def _usable(course: dict) -> bool:
    """추천할 수 있는 과정: 종료 예정이 아니고 학습 목표가 있다."""
    return not course["retiring"].strip() and bool(course["objectives"].strip())


@lru_cache(maxsize=1)
def _foundation() -> dict[str, list[str]]:
    return json.loads((ROOT / "data" / "foundation_courses.json").read_text(encoding="utf-8"))["courses"]


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


def _pick_subskill(course_id: str, subskills: list[str]) -> str | None:
    """과정이 연결된 세부 능력 중 하나. 기술의 세부 능력이 있으면 그것, 없으면 확실한 연결, 없으면 첫 연결, 연결이 없으면 None."""
    links = _links_by_course().get(course_id, [])
    for sid in subskills:
        if any(r["subskill_id"] == sid for r in links):
            return sid
    certain = [r for r in links if r["match"] == "certain"]
    pool = certain or links
    return pool[0]["subskill_id"] if pool else None


def search_courses(skill: str, k: int = 3) -> list[dict]:
    """기술 이름(대표명이나 별칭)으로 과정 후보 최대 k개. 없으면 빈 목록."""
    canon = normalize_skill(skill) or skill
    names = _names(canon)
    subskills = subskills_of(skill)
    if not subskills:  # 세부 능력이 없는 넓은 기술은 지정한 입문 과정(없으면 빈 목록)
        return [{"course_id": cid, "subskill_id": None, "match": "uncertain"}
                for cid in _foundation().get(canon, [])[:k]]

    out: list[dict] = []
    seen: set[str] = set()

    # 1) 이름이 과정의 주제로 나오는 과정: certain. 근거는 셋 중 하나다.
    #    - 제목에 나온다
    #    - 학습 목표에 나오고, 그 기술의 세부 능력에도 연결된 과정이다
    #    - 학습 목표와 설명에 둘 다 나온다
    #    학습 목표에 한 번 스친 것(예: 데이터 과정이 IaC 를 한 줄 언급)은 주제가 아니라서 세우지 않는다.
    #    정렬: 그 기술의 세부 능력에 연결된 과정 먼저 → 기초 먼저 → 제목에 나오는 과정 먼저
    named = []
    for c in _courses().values():
        if not _usable(c):
            continue
        in_title = _mentions(c["title"], names)
        in_objectives, in_description = _mentions(c["objectives"], names), _mentions(c["description"], names)
        linked = any(r["subskill_id"] in subskills for r in _links_by_course().get(c["course_id"], []))
        if in_title or (in_objectives and (linked or in_description)):
            named.append((not linked, LEVEL_ORDER.get(c["level"], 9), not in_title, c["title"], c["course_id"]))
    for *_, cid in sorted(named):
        if len(out) == k:
            return out
        out.append({"course_id": cid, "subskill_id": _pick_subskill(cid, subskills), "match": "certain"})
        seen.add(cid)

    # 2) 자리가 남으면 같은 세부 능력의 과정: uncertain(그 과정이 이 기술을 다룬다는 증거는 없다)
    rows = [r for sid in subskills for r in _links_by_subskill().get(sid, [])]
    rows.sort(key=lambda r: (r["match"] != "certain", int(r["search_rank"] or 99)))
    for r in rows:
        if len(out) == k:
            break
        cid = r["course_id"]
        if cid in seen or not _usable(_courses()[cid]):
            continue
        seen.add(cid)
        out.append({"course_id": cid, "subskill_id": r["subskill_id"], "match": "uncertain"})
    return out
