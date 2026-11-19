"""기술 → 교육과정 후보 (#14). 규칙(연결표와 과정 본문)이 먼저이고, 자리가 남으면 키워드+임베딩 검색으로 채운다.

순서
1. 기술 → 세부 능력: pipeline/courses/queries.csv(클라우드 C*)와 queries_ai_engineer.csv(AI A*, 2026-11-19)의
   aliases 에 기술 이름이 '그대로' 있는 세부 능력. 두 파일의 별칭은 겹치지 않게 둔다(S3·Docker 같은 공통 기술은
   클라우드 세부 능력으로만 이어져서, AI 를 더해도 클라우드 기술의 추천 결과가 바뀌지 않는다).
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
  subskill_id 가 None 인 항목이 있다(AWS 같은 지정 입문 과정, 임베딩으로 채운 과정 중 세부 능력 연결이 없는 것).
  ai/recommend.py 의 candidate_courses 는 sid 가 None 이면 행 조회를 건너뛰고 match 를 uncertain 으로 둔다.
- 방식(mode): rules(위 규칙만), keyword(본문 키워드 점수만), embedding(과정 벡터와 코사인만), hybrid(키워드+의미를 RRF 로 합침),
  final(기본: 규칙 결과를 먼저 주고 자리가 남으면 hybrid 로 채움, 채운 것은 match=uncertain). 비교표는 pipeline/eval_search.py.
  final 은 벡터를 쓸 수 없으면(가짜 모드, data/courses/course_vectors.json 없음, EMBED_MODEL_ID 불일치) 규칙 결과 그대로다.
  AWS 처럼 사람이 입문 과정을 지정한 기술은 채우지 않는다. Reranking 은 하지 않는다(2026-11-16 결정).
  채우는 단계는 보조다. 그 단계에서 Titan 호출 등이 실패하면 오류로 멈추지 않고 경고를 남긴 뒤 규칙 결과를 돌려준다(예전 동작과 같다).
  mode 를 직접 고른 호출(keyword 외)은 실패를 그대로 낸다.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from pipeline.aliases import ALIAS_FILE, _key, normalize_skill

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "pipeline" / "courses" / "queries.csv"
QUERY_FILES = {  # 직무별 세부 능력 검색어
    "cloud_engineer": QUERIES,
    "ai_engineer": ROOT / "pipeline" / "courses" / "queries_ai_engineer.csv",
}
COURSES = ROOT / "data" / "courses" / "courses.csv"
LINKS = ROOT / "data" / "courses" / "course_subskill.csv"
LEVEL_ORDER = {"기초": 0, "중급": 1}  # 신입 대상이라 기초부터


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=None)
def _subskills_by_alias(job_category: str | None = None) -> dict[str, list[str]]:
    """정규화한 별칭 → 세부 능력 id 목록. job_category 가 None 이면 모든 직무."""
    paths = QUERY_FILES.values() if job_category is None else [QUERY_FILES[job_category]]
    out: dict[str, list[str]] = {}
    for row in (r for path in paths for r in _read(path)):
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


def subskills_of(skill: str, job_category: str | None = None) -> list[str]:
    """기술(대표명이나 별칭)이 속한 세부 능력 id 목록. 없으면 빈 목록.

    job_category 를 주면 그 직무의 검색어 파일만 본다(예: 클라우드 수준 진단은 C* 만). 과정 검색은 모든 직무를 본다.
    """
    names = _names(normalize_skill(skill) or skill)
    out: list[str] = []
    for name in names:
        for sid in _subskills_by_alias(job_category).get(_key(name), []):
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


def _rules(skill: str, k: int) -> list[dict]:
    """규칙만으로 찾은 과정 후보 최대 k개(세부 능력 연결, 이름이 본문에 나오는 과정)."""
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


# ── 키워드 + 의미(임베딩) 검색 ─────────────────────────────────────────
VECTORS = ROOT / "data" / "courses" / "course_vectors.json"
MODES = ("rules", "keyword", "embedding", "hybrid", "final")
KEYWORD_WEIGHTS = (("title", 3.0), ("aws_services", 2.0), ("objectives", 1.5), ("description", 1.0))
KEYWORD_MIN = 3.0  # 제목에 나오거나(3.0) AWS 서비스와 학습 목표에 함께 나올 때(3.5)만 후보다. 학습 목표·설명에 스친 것은 주제가 아니다
#                    (예: Azure 가 멀티 클라우드 비용 과정의 데이터 출처로, react 가 일반 영어 단어로 걸렸다). 평가 세트로 1.5~3.0 을 비교해 정했다
SEM_MIN = 0.60  # 의미 점수(코사인)가 이 값 미만이면 후보가 아니다. 평가 세트에서 비어야 하는 기술의 최고 점수는 0.578(Git)이었다.
#                 정답 34기술로 정한 값이라 기술이 늘면 다시 본다(과적합 위험)
SEM_TOP = 10  # 의미 순위에 올리는 최대 개수
RRF_K = 60  # Reciprocal Rank Fusion 상수(순위 기반이라 점수 단위가 달라도 합칠 수 있다)


@lru_cache(maxsize=1)
def _vectors() -> dict[str, list[list[float]]] | None:
    """과정 id → 청크 벡터 목록. 파일이 없거나 만든 모델이 지금 EMBED_MODEL_ID 와 다르면 None."""
    from pipeline import embed

    embed.is_mock()  # .env 를 읽어 EMBED_MODEL_ID 를 환경 변수로 올린다
    if not VECTORS.exists():
        return None
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    if data["model"] != os.getenv("EMBED_MODEL_ID"):
        return None
    out: dict[str, list[list[float]]] = {}
    for item in data["items"]:
        out.setdefault(item["course_id"], []).append(item["vector"])
    return out


def _semantic_ready() -> bool:
    from pipeline import embed

    return not embed.is_mock() and _vectors() is not None


def _query_text(skill: str) -> str:
    names = _names(normalize_skill(skill) or skill)
    return f"AWS training course about {', '.join(names[:4])}"


def _tie_key(cid: str) -> tuple:
    c = _courses()[cid]
    return (LEVEL_ORDER.get(c["level"], 9), c["title"])  # 동률이면 기초 먼저(신입 대상)


def _keyword_ranking(skill: str) -> list[str]:
    """이름이 제목·AWS 서비스·학습 목표·설명에 나오는 정도(필드 가중치)로 매긴 순위. KEYWORD_MIN 미만은 뺀다."""
    names = [n for n in _names(normalize_skill(skill) or skill) if len(n) >= 3]  # 'Go' 같은 짧은 이름은 일반 단어에 걸린다
    scores = {}
    for c in _courses().values():
        if _usable(c):
            score = sum(w for f, w in KEYWORD_WEIGHTS if names and _mentions(c[f], names))
            if score >= KEYWORD_MIN:
                scores[c["course_id"]] = score
    return sorted(scores, key=lambda cid: (-scores[cid], *_tie_key(cid)))


def _semantic_ranking(skill: str) -> list[str]:
    """질의 벡터와 과정 청크 벡터의 코사인(과정별 가장 높은 청크)으로 매긴 순위. SEM_MIN 미만은 뺀다."""
    from pipeline import embed

    query = embed.embed(_query_text(skill))
    scores = {cid: max(embed.cosine(query, v) for v in vs) for cid, vs in _vectors().items()
              if _usable(_courses()[cid])}
    kept = [cid for cid in scores if scores[cid] >= SEM_MIN]
    return sorted(kept, key=lambda cid: (-scores[cid], *_tie_key(cid)))[:SEM_TOP]


def _fuse(*rankings: list[str]) -> list[str]:
    """Reciprocal Rank Fusion: 여러 순위에서 위에 있을수록 점수가 높다."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for i, cid in enumerate(ranking):
            score[cid] = score.get(cid, 0.0) + 1 / (RRF_K + i + 1)
    return sorted(score, key=lambda cid: (-score[cid], *_tie_key(cid)))


def _candidates(skill: str, mode: str) -> list[str]:
    if mode == "keyword":
        return _keyword_ranking(skill)
    if not _semantic_ready():
        raise RuntimeError(f"mode={mode!r} 는 실제 모드와 과정 벡터(course_vectors.json, EMBED_MODEL_ID 일치)가 필요합니다")
    if mode == "embedding":
        return _semantic_ranking(skill)
    return _fuse(_keyword_ranking(skill), _semantic_ranking(skill))


def _linked_subskill(cid: str, subskills: list[str]) -> str | None:
    """그 기술의 세부 능력에 과정이 실제로 연결돼 있으면 그 id, 아니면 None(규칙으로 찾은 연결이 아니다)."""
    links = _links_by_course().get(cid, [])
    return next((sid for sid in subskills if any(r["subskill_id"] == sid for r in links)), None)


def search_courses(skill: str, k: int = 3, mode: str = "final") -> list[dict]:
    """기술 이름(대표명이나 별칭)으로 과정 후보 최대 k개. 없으면 빈 목록. mode 는 모듈 설명 참고."""
    if mode not in MODES:
        raise ValueError(f"지원하지 않는 mode: {mode!r} (가능: {', '.join(MODES)})")
    if not skill.strip():
        return []
    if mode == "rules":
        return _rules(skill, k)
    subskills = subskills_of(skill)
    if mode == "final":
        out = _rules(skill, k)
        if len(out) >= k or (normalize_skill(skill) or skill) in _foundation() or not _semantic_ready():
            return out  # 사람이 지정한 입문 과정(AWS)은 채우지 않는다
        seen = {f["course_id"] for f in out}
        try:
            filler = _candidates(skill, "hybrid")
        except Exception as e:  # ponytail: 어떤 실패든 규칙 결과로 돌아간다. 오류 종류를 가려야 하면 botocore 예외로 좁힌다
            logging.getLogger(__name__).warning("채움 단계(임베딩 검색) 실패, 규칙 결과만 돌려줍니다: %s: %s", type(e).__name__, e)
            return out
        for cid in filler:
            if len(out) == k:
                break
            if cid not in seen:
                seen.add(cid)
                out.append({"course_id": cid, "subskill_id": _linked_subskill(cid, subskills), "match": "uncertain"})
        return out
    return [{"course_id": cid, "subskill_id": _linked_subskill(cid, subskills), "match": "uncertain"}
            for cid in _candidates(skill, mode)[:k]]
