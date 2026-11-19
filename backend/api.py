"""화면(app)이 부르는 함수 모음.

지금(M1)은 BEDROCK_MOCK=1 이면 data/sample 의 가짜 데이터를 돌려준다.
함수 이름과 반환 형식은 그대로 두고, 담당자가 실제 구현으로 교체한다.

| 함수                 | 반환 형식           | 실제 구현 담당          |
| -------------------- | ------------------- | ----------------------- |
| get_demand           | ④ 수요 집계          | 일현 (#12)              |
| get_gap              | Gap 목록            | 일현 (#12)              |
| get_area_gap         | 영역별 Gap 목록      | 일현 (#12, #17)         |
| get_market_gap       | 시장 Gap 표          | 담당 미정 (⑤ 공급 데이터) |
| get_recommendations  | ③ 추천 목록          | 종훈·성진 (#14, #15) 연결됨 |
| extract_posting      | ② 추출 결과          | 성진·일현 (#9, #11)      |

형식은 contracts/README.md 를 따른다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import monotonic

from backend.dynamodb import DynamoDBStore, json_numbers
from backend.jobs import DEFAULT_JOB, JOBS, job_file
from backend.levels import (course_level_first, level_after_courses, required_level, satisfaction, skill_levels,
                            whole)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLE_DIR = DATA_DIR / "sample"
DEMAND_CACHE_SECONDS = 60
_demand_cache: dict[tuple[str, str, str, str], tuple[float, dict]] = {}
_demand_cache_lock = Lock()
# 실제 모드 추천: 부족 기술 중 앞(수요 높은 순)에서 몇 개만 Bedrock 으로 추천할지. 기술마다 호출 1번(비용·화면 길이)
RECOMMEND_TOP_N = 3
# (직무, (기술, postings, of, required_ratio)...) → ③ 목록. 수요 숫자가 바뀌면 키가 달라져 다시 부른다.
# 최근에 쓴 RECOMMEND_CACHE_MAX 개만 두고, 넘으면 가장 오래 안 쓴 것부터 버린다
RECOMMEND_CACHE_MAX = 32
_recommend_cache: OrderedDict[tuple, list[dict]] = OrderedDict()
_recommend_cache_lock = Lock()

logger = logging.getLogger(__name__)

UNCLASSIFIED = "미분류"

# 가짜 모드에서만 쓰는 최소 별칭 표. 실제 표준화는 pipeline 의 별칭 사전(#13)이 맡는다.
_MOCK_ALIASES = {
    "아마존 웹 서비스": "AWS",
    "리눅스": "Linux",
    "파이썬": "Python",
    "도커": "Docker",
    "쿠버네티스": "Kubernetes",
    "EKS": "Kubernetes",
    "IaC": "Terraform",
}


def is_mock() -> bool:
    return os.getenv("BEDROCK_MOCK", "1") == "1"


def _load(name: str, job_category: str = DEFAULT_JOB):
    """data/sample 의 가짜 데이터. 직무별 파일이 있으면 그것을 읽는다(backend.jobs.job_file)."""
    path = SAMPLE_DIR / job_file(name, job_category)
    if not path.exists():
        path = SAMPLE_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def get_demand(job_category: str) -> dict:
    """직무 수요 집계(④). 실제 모드는 완성된 DB 결과를 60초간 재사용한다."""
    if is_mock():
        return _load("demand.json", job_category)
    table_name = os.getenv("DYNAMODB_DEMAND_TABLE", "").strip()
    if not table_name:
        raise ValueError("DYNAMODB_DEMAND_TABLE을 설정하세요.")
    cache_key = (
        job_category, table_name,
        os.getenv("AWS_REGION", "ap-northeast-2"), os.getenv("AWS_PROFILE", ""),
    )
    with _demand_cache_lock:
        now = monotonic()
        cached = _demand_cache.get(cache_key)
        if cached is not None and now - cached[0] < DEMAND_CACHE_SECONDS:
            return deepcopy(cached[1])
        snapshot = DynamoDBStore.from_env().get_demand_snapshot(job_category)
        if snapshot is None:
            raise LookupError(f"완성된 직무 수요 집계가 없습니다: {job_category}")
        demand = json_numbers(snapshot["demand"])
        _demand_cache[cache_key] = (monotonic(), demand)
        return deepcopy(demand)


@lru_cache
@lru_cache(maxsize=None)
def _skill_areas(job_category: str = DEFAULT_JOB) -> dict:
    """표준 기술명 → 영역 사전(data/skill_areas.json, AI 는 skill_areas_ai_engineer.json). 배정은 사람이 정하고 코드는 조회만 한다."""
    return json.loads((DATA_DIR / job_file("skill_areas.json", job_category)).read_text(encoding="utf-8"))


def get_areas(job_category: str) -> dict[str, str]:
    """그 직무의 영역 {id: 이름}(공통 제외). 수준 진단 화면이 영역별로 질문을 나눌 때 쓴다."""
    return {k: v for k, v in _skill_areas(job_category)["areas"].items() if k != "COMMON"}


def _area_of(skill: str, job_category: str = DEFAULT_JOB) -> tuple[str, str]:
    """(영역 id, 영역 이름). 사전에 없으면 미분류."""
    table = _skill_areas(job_category)
    area_id = table["skills"].get(skill)
    return (area_id, table["areas"][area_id]) if area_id else ("UNCLASSIFIED", UNCLASSIFIED)


def get_gap(job_category: str, owned_skills, min_ratio: float = 0.2) -> list[dict]:
    """공고 비율이 min_ratio 이상인 기술을 수요 높은 순으로 돌려주고, 내 단계와 보유 여부를 표시한다.

    owned_skills 는 {세부 능력 id: 0~5}(보유 수준 진단) 또는 기술 이름 목록(예전 형식, 상담 채팅)이다.
    단계·기준 단계·충족도 계산은 backend/levels.py 에 있다. 계산은 전부 코드로 한다. LLM 을 쓰지 않는다.
    """
    demand = get_demand(job_category)
    total = demand["total_postings"]
    picked = [s for s in demand["skills"] if (s["postings"] / total if total else 0) >= min_ratio]
    mine = skill_levels(owned_skills, [s["skill"] for s in picked], job_category)
    rows = []
    for s in picked:
        level = mine[s["skill"]]
        need, confidence = required_level(s["skill"], job_category)
        rows.append({
            "skill": s["skill"],
            "area": _area_of(s["skill"], job_category)[1],
            "postings": s["postings"],
            "of": total,
            "ratio": round(s["postings"] / total, 2),
            "required_ratio": round(s["required"] / s["postings"], 2) if s["postings"] else 0,
            "owned": level >= need,
            "user_level": whole(level),
            "required_level": need,
            "required_level_confidence": confidence,
            "satisfaction": satisfaction(level, need, s.get("required", 0), s.get("preferred", 0)),
        })
    rows.sort(key=lambda r: r["postings"], reverse=True)
    return rows


def owned_skill_names(job_category: str, owned_skills) -> list[str]:
    """수요 집계의 기술 중 내 단계가 기준 단계 이상인 기술 이름(수요 높은 순). 상담 채팅과 시장 Gap 표에 쓴다."""
    skills = [s["skill"] for s in get_demand(job_category)["skills"]]
    mine = skill_levels(owned_skills, skills, job_category)
    return [s for s in skills if mine[s] >= required_level(s, job_category)[0]]


def get_area_gap(job_category: str, owned_skills) -> list[dict]:
    """영역(능력 범주)마다 공고 수요와 그중 내가 채운 몫을 돌려준다. 계산은 전부 코드로 한다.

    - demand: 그 영역 기술들의 요구 공고 수를 더한 값. 한 공고가 영역의 기술 둘을 요구하면 2로 센다.
    - owned: 기술마다 요구 공고 수 × 충족도(backend/levels.py)를 더한 값. 소수일 수 있다.
      coverage = owned / demand (demand 가 0이면 0). 기준 단계에 못 미친 기술은 missing_skills 에 남는다.
    영역 순서는 data/skill_areas.json 의 순서이고, 사전에 없는 기술은 마지막의 미분류에 모은다.
    """
    demand = get_demand(job_category)
    skills = [s["skill"] for s in demand["skills"]]
    return _area_rows(demand, skill_levels(owned_skills, skills, job_category), job_category)


def _area_rows(demand: dict, mine: dict[str, float], job_category: str) -> list[dict]:
    """get_area_gap 의 계산. mine 은 {기술: 내 단계}."""
    rows = {
        area_id: {"area_id": area_id, "area": name, "demand": 0, "owned": 0.0, "skills": [], "missing_skills": []}
        for area_id, name in [*_skill_areas(job_category)["areas"].items(), ("UNCLASSIFIED", UNCLASSIFIED)]
    }
    for s in demand["skills"]:
        row = rows[_area_of(s["skill"], job_category)[0]]
        level = mine[s["skill"]]
        need, _ = required_level(s["skill"], job_category)
        row["demand"] += s["postings"]
        row["skills"].append(s["skill"])
        row["owned"] += s["postings"] * satisfaction(level, need, s.get("required", 0), s.get("preferred", 0))
        if level < need:
            row["missing_skills"].append(s["skill"])
    for row in rows.values():
        row["coverage"] = round(row["owned"] / row["demand"], 2) if row["demand"] else 0
        row["owned"] = whole(round(row["owned"], 1))
    return list(rows.values())


def get_area_gap_after(job_category: str, owned_skills, recommendations: list[dict]) -> list[dict]:
    """추천 과정을 모두 마쳤다고 볼 때의 영역별 Gap(예상, #91). 계산은 전부 코드로 한다.

    recommendations 는 get_recommendations 결과다. "확실"한 과정만 마친 것으로 친다("불확실"은 넣지 않는다, 원칙 4).
    마친 기술의 예상 단계는 과정 난이도로 정한다(backend.levels.level_after_courses: 기초 2단계, 중급 3단계).
    지금 단계가 더 높으면 그대로 둔다. 형식은 get_area_gap 과 같고, 영역마다 raised 가 더 붙는다:
    [{"skill", "from", "to"}] 단계가 오르는 기술. 과정을 마친다고 그 단계가 된다는 보장은 없다. 화면에는 "예상"이라고 적는다.
    """
    demand = get_demand(job_category)
    skills = [s["skill"] for s in demand["skills"]]
    now = skill_levels(owned_skills, skills, job_category)
    after = dict(now)
    for rec in recommendations:
        if rec["gap_skill"] not in after:
            continue
        done = [c.get("level", "") for c in rec["courses"] if c.get("match") == "certain"]
        after[rec["gap_skill"]] = level_after_courses(now[rec["gap_skill"]], done)
    rows = _area_rows(demand, after, job_category)
    for row in rows:
        row["raised"] = [{"skill": s, "from": whole(now[s]), "to": whole(after[s])} for s in row["skills"] if after[s] > now[s]]
    return rows


def _get_supply() -> dict:
    """교육 공급 집계(⑤): 기술별로 그 기술을 다루는 교과목 수."""
    if is_mock():
        return _load("supply.json")
    raise NotImplementedError("실제 교과목 공급 집계는 데이터와 담당이 정해지면 구현합니다.")


def _shares(rates: dict[str, float]) -> dict[str, float]:
    """{기술: 값} → {기술: 전체 합에서 차지하는 비중(0~1)}"""
    total = sum(rates.values()) or 1.0
    return {k: v / total for k, v in rates.items()}


def get_market_gap(job_category: str) -> dict:
    """시장 Gap: 공고는 원하는데 교육이 덜 다루는 기술. 5주차 실습 정의를 그대로 따른다.

    - 수요율 = 가중치 합 / 공고 수. 가중치 표는 ai/sections.py 한 곳에만 둔다(팀 기준: 칸이 있으면 칸, 없으면 type).
      ④에는 칸별 건수가 없어 지금은 type 기준(필수·우대 건수)으로만 계산한다.
      ④에 가중 합이 들어오면(docs/development.md 의 미정 항목) 그 값을 쓰도록 바꾼다.
    - 공급율 = 그 기술을 다루는 교과목 수 / 전체 교과목 수
    - 비중 = 각 값 / 전체 합. Gap(%p) = (수요 비중 - 공급 비중) × 100
    + 이면 시장은 원하는데 교육이 덜 다루는 기술, - 이면 교육이 상대적으로 많이 다루는 기술이다.
    rows 는 Gap 이 큰 순서. 계산은 전부 코드로 한다.
    """
    from ai.sections import SECTION_WEIGHT, TYPE_WEIGHT

    demand = get_demand(job_category)
    supply = _get_supply()
    total = demand["total_postings"]
    demand_rate = {
        s["skill"]: sum(w * s.get(kind, 0) for kind, w in TYPE_WEIGHT.items()) / total if total else 0
        for s in demand["skills"]
    }
    courses = supply["total_courses"]
    supply_rate = {s["skill"]: s["courses"] / courses if courses else 0 for s in supply["skills"]}
    d_share, s_share = _shares(demand_rate), _shares(supply_rate)
    rows = []
    for skill in sorted(set(d_share) | set(s_share)):
        d, p = d_share.get(skill, 0) * 100, s_share.get(skill, 0) * 100
        rows.append({"skill": skill, "demand_pct": round(d, 1), "supply_pct": round(p, 1), "gap_pp": round(d - p, 1)})
    rows.sort(key=lambda r: r["gap_pp"], reverse=True)
    return {
        "weights": {"section": dict(SECTION_WEIGHT), "type": dict(TYPE_WEIGHT), "applied": "type"},
        "total_postings": total,
        "supply": {k: supply.get(k) for k in ("source", "total_courses", "note")},
        "rows": rows,
    }


def get_recommendations(job_category: str, gap_skills: list[str], *, review: bool = False,
                        levels: dict[str, float] | None = None) -> list[dict]:
    """부족 기술마다 추천(③)을 돌려준다. 찾은 과정이 없으면 courses 가 빈 목록이다.

    levels 는 부족 기술의 내 단계 {기술: 0~5}(get_gap 의 user_level)다. 주면 추천마다 user_level 을 적고,
    0~2단계는 기초, 3단계 이상은 중급 과정을 앞에 놓는다(확실한 과정이 불확실한 과정보다 먼저).
    과정을 새로 찾거나 빼지 않고 순서만 바꾼다. 주지 않으면 전과 같다.

    가짜 모드는 data/sample 의 추천을 읽는다. 실제 모드는 수요 숫자를 코드로 만들고(get_demand),
    과정 찾기와 추천 이유는 ai.recommend 가 맡는다(Bedrock 은 reason 만 쓴다).
    실제 모드는 gap_skills 앞에서부터(화면은 수요 높은 순으로 준다) RECOMMEND_TOP_N 개만 추천하고,
    같은 직무·같은 기술·같은 수요 숫자면 Bedrock 을 다시 부르지 않고 캐시를 돌려준다.
    채팅에서 review=True이면 과정 목표로 사유를 검증한다. 모의 모드에서도 로컬 과정 목록을 쓴다.
    """
    mock = is_mock()
    demand_all = get_demand(job_category)
    by_demand = {s["skill"]: s for s in demand_all["skills"]}
    if not mock or review:
        gap_skills = gap_skills[:RECOMMEND_TOP_N]  # 기술별 모델 호출 비용과 화면 길이를 제한한다
    pairs = []
    for skill in gap_skills:
        d = by_demand.get(skill, {"postings": 0, "required": 0})  # 수요에 없는 기술은 0 으로 넘긴다
        pairs.append((skill, {
            "postings": d["postings"],
            "of": demand_all["total_postings"],
            "required_ratio": round(d["required"] / d["postings"], 2) if d["postings"] else 0,
        }))
    if mock and not review:
        by_skill = {r["gap_skill"]: r for r in _load("recommendations.json", job_category)}
        return [_with_level(by_skill[skill] if skill in by_skill else {"gap_skill": skill, "demand": demand, "courses": []},
                            levels)
                for skill, demand in pairs]

    key = (job_category, review, mock,
           tuple((skill, d["postings"], d["of"], d["required_ratio"]) for skill, d in pairs))
    with _recommend_cache_lock:
        cached = _recommend_cache.get(key)
        if cached is not None:
            _recommend_cache.move_to_end(key)  # 방금 쓴 것으로 표시
    if cached is not None:  # 단계는 캐시 키에 넣지 않는다. 과정과 이유는 단계와 무관하고 순서만 바꾼다
        return [_with_level(rec, levels) for rec in deepcopy(cached)]

    from ai.bedrock import BedrockJSONError
    from ai.recommend import build_recommendation

    out = []
    failed_review = None
    unavailable = None
    for skill, demand in pairs:
        if review:
            try:
                rec, result = build_recommendation(skill, demand, review=True)
            except (BedrockJSONError, KeyError, TypeError, ValueError) as exc:
                failed_review = exc
                continue
            except Exception as exc:
                unavailable = exc
                continue
        else:
            rec, result = build_recommendation(skill, demand)
        out.append(rec)
        if result is not None:
            logger.info("추천 %s: 시도 %d회, 토큰 %s", skill, result.attempts, result.usage.get("totalTokens", 0))
    if unavailable is not None or failed_review is not None:
        if any(rec["courses"] for rec in out):
            return [_with_level(rec, levels) for rec in out]
        if unavailable is not None:
            raise unavailable
        raise failed_review
    with _recommend_cache_lock:  # 실패하면 여기까지 오지 않으므로 오류는 캐시하지 않는다
        _recommend_cache[key] = deepcopy(out)
        _recommend_cache.move_to_end(key)
        while len(_recommend_cache) > RECOMMEND_CACHE_MAX:
            _recommend_cache.popitem(last=False)
    return [_with_level(rec, levels) for rec in deepcopy(out)]


def _with_level(rec: dict, levels: dict[str, float] | None) -> dict:
    """추천 1건에 user_level 을 적고, 단계에 맞는 난이도의 과정을 앞으로 옮긴다. levels 가 없으면 그대로 둔다.

    "불확실" 과정이 "확실" 과정을 앞지르지 않는다. 같은 조건 안의 순서는 그대로 둔다(정렬은 안정적이다).
    """
    if levels is None or rec["gap_skill"] not in levels:
        return rec
    level = levels[rec["gap_skill"]]
    first = course_level_first(level)
    courses = sorted(rec["courses"], key=lambda c: (c.get("match") == "uncertain", c.get("level") != first))
    return {**rec, "user_level": whole(level), "courses": courses}


def extract_posting(text: str, posting_id: str = "manual-input") -> dict:
    """공고 본문에서 기술, 필수/우대, 근거 문장을 뽑는다(②).

    가짜 모드에서는 키워드를 찾아 그 구절을 근거로 쓴다. 실제 모드는 ai.extract(#9)의 Bedrock 추출을 부른다.
    """
    if not is_mock():
        from ai.extract import extract_skills

        return extract_skills({"id": posting_id, "text": text})
    from ai.sections import EXCLUDED, find_section

    # 두 직무의 가짜 수요 기술을 모두 찾는다(AI 공고도 붙여 넣을 수 있게)
    vocab = {s["skill"]: s["skill"] for job in JOBS for s in _load("demand.json", job)["skills"]}
    vocab.update(_MOCK_ALIASES)
    clauses = [c.strip() for c in re.split(r"[.\n,·;]", text) if c.strip()]
    skills, seen = [], set()
    for clause in clauses:
        section = find_section(text, clause)
        if section == EXCLUDED:
            continue  # 혜택·회사소개 칸은 지원자에게 요구하는 기술이 아니다
        for raw, normalized in vocab.items():
            if raw.lower() in clause.lower() and normalized not in seen:
                seen.add(normalized)
                skills.append({
                    "raw": raw,
                    "normalized": normalized,
                    "type": "preferred" if "우대" in clause else "required",
                    "section": section,
                    "evidence": clause,
                    "confidence": "high" if raw == normalized else "uncertain",
                    "ncs_unit": None,
                })
    return {"posting_id": posting_id, "skills": skills}
