"""기술 → 교육과정 후보 (#14). 규칙으로 확인한 과정부터 추천하고, 실제 모드에서는 KB와 키워드 순위도 반영한다.

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
- 방식(mode): rules(위 규칙만), keyword(본문 키워드), embedding(Bedrock KB 의미 검색), hybrid(키워드와 KB 순위를 RRF로 합침),
  final(기본: certain 과정부터 추천하고 각 그룹 내에서는 키워드와 KB 순위를 반영). 비교표는 pipeline/eval_search.py.
  희망 난이도가 있으면 확장 목록에서 해당 단계의 과정만 남기고 주제 근거와 KB 관련도로 후보를 고른다.
  가짜 모드의 final은 규칙만 사용한다.
  AWS처럼 사람이 지정한 입문 과정은 KB에 있는 지정 과정만 원래 순서로 준다. Reranking은 하지 않는다(2026-11-16 결정).
  실제 모드에서 KB 검색이 실패하면 호출자에게 오류를 전달한다.
- 비교용 방식: bm25(BM25 키워드만, KB 불필요), hybrid_bm25(BM25+KB 의미를 RRF 로), final_bm25(final 과 같고 hybrid 자리만 hybrid_bm25).
  기본값은 final 이다. 비교용이라 채택하지 않았다. KB 방식 기준의 비교 수치는 pipeline/eval_search.py 로 다시 재서 정한다.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import Counter
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
COURSE_LEVELS = ("기초", "중급", "고급")
LEVEL_ORDER = {level: index for index, level in enumerate(COURSE_LEVELS)}


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


@lru_cache(maxsize=2)
def expanded_course_catalog(base: Path) -> dict[str, dict]:
    """Load course details shared by difficulty search and the recommendation writer."""
    expanded = base.with_name("catalog_all.csv")
    primary = _read(base) if base.is_file() or not expanded.is_file() else []
    rows = {row["course_id"]: row for row in primary}
    if expanded.is_file():
        rows.update((row["course_id"], row) for row in _read(expanded))
    return rows


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


def _rules(skill: str, k: int, *, catalog: dict[str, dict] | None = None) -> list[dict]:
    """규칙만으로 찾은 과정 후보 최대 k개(세부 능력 연결, 이름이 본문에 나오는 과정)."""
    canon = normalize_skill(skill) or skill
    names = _names(canon)
    subskills = subskills_of(skill)
    if not subskills and catalog is None:  # 넓은 기술의 기본 추천은 지정한 입문 과정
        return [{"course_id": cid, "subskill_id": None, "match": "uncertain"}
                for cid in _foundation().get(canon, [])[:k]]
    if catalog is None:
        catalog = _courses()

    out: list[dict] = []
    seen: set[str] = set()

    # 1) 이름이 과정의 주제로 나오는 과정: certain. 근거는 셋 중 하나다.
    #    - 제목에 나온다
    #    - 학습 목표에 나오고, 그 기술의 세부 능력에도 연결된 과정이다
    #    - 학습 목표와 설명에 둘 다 나온다
    #    학습 목표에 한 번 스친 것(예: 데이터 과정이 IaC 를 한 줄 언급)은 주제가 아니라서 세우지 않는다.
    #    정렬: 그 기술의 세부 능력에 연결된 과정 먼저 → 기초 먼저 → 제목에 나오는 과정 먼저
    named = []
    for c in catalog.values():
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
        if cid in seen or cid not in catalog or not _usable(catalog[cid]):
            continue
        seen.add(cid)
        out.append({"course_id": cid, "subskill_id": r["subskill_id"], "match": "uncertain"})
    return out


# ── 키워드 + 의미(임베딩) 검색 ─────────────────────────────────────────
MODES = ("rules", "keyword", "bm25", "embedding", "hybrid", "hybrid_bm25", "final", "final_bm25")
KEYWORD_WEIGHTS = (("title", 3.0), ("aws_services", 2.0), ("objectives", 1.5), ("description", 1.0))
KEYWORD_MIN = 3.0  # 제목에 나오거나(3.0) AWS 서비스와 학습 목표에 함께 나올 때(3.5)만 후보다. 학습 목표·설명에 스친 것은 주제가 아니다
#                    (예: Azure 가 멀티 클라우드 비용 과정의 데이터 출처로, react 가 일반 영어 단어로 걸렸다). 평가 세트로 1.5~3.0 을 비교해 정했다
KB_MIN_SCORE = 0.78  # 34개 기술 초안에서 KB가 추가 오추천을 만들지 않은 관련도 하한. 카탈로그 변경 시 재평가한다.
SEM_TOP = 10  # 의미 순위에 올리는 최대 개수
RRF_K = 60  # Reciprocal Rank Fusion 상수(순위 기반이라 점수 단위가 달라도 합칠 수 있다)


@lru_cache(maxsize=1)
def _kb_client(region: str):
    import boto3

    return boto3.client("bedrock-agent-runtime", region_name=region)


def _semantic_ready() -> bool:
    from pipeline import embed

    return not embed.is_mock()


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


BM25_K1, BM25_B = 1.5, 0.75  # 표준값. 과정 158개로는 조정할 근거가 없어 그대로 둔다
BM25_MIN = 4.0  # BM25 점수가 이 값 이하면 후보가 아니다. 정답 34기술 평가로 0~10 을 비교해 hit 가 유지되는 가장 높은 값을 골랐다(과적합 위험).
#                 그래도 비어야 하는 기술 11개 중 4개(Azure·GCP·React·Linux)에 결과가 나온다. BM25 점수는 절대 기준이 아니라서 임계값으로 못 거른다
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.-]*|[가-힣]+")


def _tokens(text: str) -> list[str]:
    """소문자 낱말. 문장 끝의 마침표·붙임표는 뗀다("EC2." 와 "EC2" 가 같은 낱말). 어간 처리는 하지 않는다."""
    return [t.rstrip(".-") for t in _TOKEN.findall(text.lower()) if t.rstrip(".-")]


@lru_cache(maxsize=1)
def _bm25_index() -> dict:
    """추천할 수 있는 과정의 제목 + AWS 서비스 + 학습 목표로 만든 BM25 색인. 설명은 스치는 말이 많아 뺀다(키워드 방식과 같은 판단)."""
    tf = {c["course_id"]: Counter(_tokens(f"{c['title']}. {c['aws_services']}. {c['objectives']}"))
          for c in _courses().values() if _usable(c)}
    length = {cid: sum(t.values()) for cid, t in tf.items()}
    df = Counter(w for t in tf.values() for w in t)
    n = len(tf)
    return {"tf": tf, "length": length, "avg": sum(length.values()) / n,
            "idf": {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}}


def _bm25_scores(skill: str) -> dict[str, float]:
    """질의는 기술의 대표명과 별칭을 낱말로 쪼갠 집합(같은 낱말은 한 번만 센다)."""
    idx = _bm25_index()
    terms = {w for n in _names(normalize_skill(skill) or skill) if len(n) >= 3 for w in _tokens(n)}
    scores = {}
    for cid, tf in idx["tf"].items():
        s = sum(idx["idf"][w] * tf[w] * (BM25_K1 + 1) / (tf[w] + BM25_K1 * (1 - BM25_B + BM25_B * idx["length"][cid] / idx["avg"]))
                for w in terms if w in tf)
        if s > 0:
            scores[cid] = s
    return scores


def _bm25_ranking(skill: str) -> list[str]:
    """BM25 점수 순위. BM25_MIN 이하는 뺀다. BM25 는 낱말이 하나라도 걸리면 점수가 생겨서 임계값이 없으면 비어야 할 기술에도 결과가 나온다."""
    scores = {cid: v for cid, v in _bm25_scores(skill).items() if v > BM25_MIN}
    return sorted(scores, key=lambda cid: (-scores[cid], *_tie_key(cid)))


def _kb_scores(skill: str, course_ids: list[str] | None = None, *,
               catalog: dict[str, dict] | None = None) -> dict[str, float]:
    """추천 가능한 KB 문서를 조회하고 과정마다 가장 높은 청크 점수를 반환한다."""
    kb_id = os.getenv("BEDROCK_KB_ID", "").strip()
    if not kb_id:
        raise RuntimeError("교육과정 검색에 필요한 BEDROCK_KB_ID가 설정되지 않았습니다.")
    filters = [
        {"equals": {"key": "type", "value": "program"}},
        {"equals": {"key": "recommendable", "value": True}},
    ]
    if course_ids:
        filters.append({"in": {"key": "course_id", "value": course_ids}})
    client = _kb_client(os.getenv("AWS_REGION", "ap-northeast-2"))
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": _query_text(skill)},
        retrievalConfiguration={"vectorSearchConfiguration": {
            "numberOfResults": 30,  # 한 과정에서 여러 청크가 검색될 수 있다
            "overrideSearchType": "SEMANTIC",
            "filter": {"andAll": filters},
        }},
    )
    scores: dict[str, float] = {}
    if catalog is None:
        catalog = _courses()
    for hit in response["retrievalResults"]:
        cid = hit.get("metadata", {}).get("course_id")
        score = hit.get("score", 0.0)
        if isinstance(cid, str) and cid in catalog and _usable(catalog[cid]):
            scores[cid] = max(scores.get(cid, 0.0), score)
    return scores


def _semantic_ranking(skill: str) -> list[str]:
    """KB 관련도 하한을 넘는 과정만 점수 순서로 반환한다."""
    scores = _kb_scores(skill)
    kept = [cid for cid, score in scores.items() if score >= KB_MIN_SCORE]
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
    if mode == "bm25":
        return _bm25_ranking(skill)
    if not _semantic_ready():
        raise RuntimeError(f"mode={mode!r}는 실제 모드(BEDROCK_MOCK=0)에서 교육과정 KB를 사용합니다.")
    if mode == "embedding":
        return _semantic_ranking(skill)
    if mode == "hybrid_bm25":
        return _fuse(_bm25_ranking(skill), _semantic_ranking(skill))
    return _fuse(_keyword_ranking(skill), _semantic_ranking(skill))


def _linked_subskill(cid: str, subskills: list[str]) -> str | None:
    """그 기술의 세부 능력에 과정이 실제로 연결돼 있으면 그 id, 아니면 None(규칙으로 찾은 연결이 아니다)."""
    links = _links_by_course().get(cid, [])
    return next((sid for sid in subskills if any(r["subskill_id"] == sid for r in links)), None)


def search_courses(skill: str, k: int = 3, mode: str = "final", *,
                   course_levels: tuple[str, ...] | None = None) -> list[dict]:
    """기술 이름(대표명이나 별칭)으로 과정 후보 최대 k개. 없으면 빈 목록. mode 는 모듈 설명 참고."""
    if mode not in MODES:
        raise ValueError(f"지원하지 않는 mode: {mode!r} (가능: {', '.join(MODES)})")
    if not skill.strip() or k <= 0:
        return []
    if course_levels is not None:
        if not course_levels:
            return []
        if mode not in ("rules", "final", "final_bm25"):
            raise ValueError("난이도 지정은 과정 추천 검색에서 지원합니다.")
        eligible = {
            cid: course for cid, course in expanded_course_catalog(COURSES).items()
            if course["level"] in course_levels and course.get("status", "ACTIVE") == "ACTIVE"
            and course["title"].strip() and course["url"].strip() and _usable(course)
        }
        candidates = _rules(skill, len(eligible), catalog=eligible)
        scores = {}
        if candidates and mode != "rules" and _semantic_ready():
            # The deployed KB has no level metadata. Only rank locally eligible topic matches.
            scores = _kb_scores(skill, catalog=eligible)
        candidates.sort(key=lambda c: (c["match"] != "certain", course_levels.index(eligible[c["course_id"]]["level"]),
                                       -scores.get(c["course_id"], 0)))
        return candidates[:k]
    if mode == "rules":
        return _rules(skill, k)
    subskills = subskills_of(skill)
    if mode in ("final", "final_bm25"):
        if not _semantic_ready():
            return _rules(skill, k)
        curated = _foundation().get(normalize_skill(skill) or skill)
        if curated:
            indexed = _kb_scores(skill, curated)
            return [{"course_id": cid, "subskill_id": None, "match": "uncertain"}
                    for cid in curated if cid in indexed][:k]
        hybrid = _candidates(skill, "hybrid_bm25" if mode == "final_bm25" else "hybrid")
        rules = _rules(skill, len(_courses()))
        certain = [r for r in rules if r["match"] == "certain"]
        position = {cid: index for index, cid in enumerate(hybrid)}
        certain.sort(key=lambda r: position.get(r["course_id"], len(position)))
        out = certain[:k]
        if len(out) == k:
            return out
        certain_ids = {r["course_id"] for r in certain}
        related = {r["course_id"]: r for r in rules if r["match"] == "uncertain"}
        ranked = _fuse(list(related), [cid for cid in hybrid if cid not in certain_ids])
        for cid in ranked[:k - len(out)]:
            out.append(related.get(cid) or {
                "course_id": cid, "subskill_id": _linked_subskill(cid, subskills), "match": "uncertain",
            })
        return out
    return [{"course_id": cid, "subskill_id": _linked_subskill(cid, subskills), "match": "uncertain"}
            for cid in _candidates(skill, mode)[:k]]
