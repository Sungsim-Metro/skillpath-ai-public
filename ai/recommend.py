"""부족 기술 → 추천(③) (#15). LLM 은 reason 만 쓴다.

- 과정 후보: pipeline.search.search_courses(skill) (#14). 반환 [{"course_id", "subskill_id", "match"}]
- match: search_courses 가 돌려준 값을 그대로 쓴다(그 과정이 '이 기술'을 다루는지를 search 가 판단한다).
  course_subskill.csv 의 match 는 '세부 능력'을 다룬다는 증거라 쓰지 않는다.
  subskill_id 가 None 이면(AWS 처럼 넓은 기술에 지정한 과정 등) 규칙으로 찾은 연결이 아니므로 uncertain 으로 둔다
- retiring 과정과 학습 목표(objectives)가 빈 과정은 제외한다 (근거로 삼을 내용이 없음)
- 입력 숫자는 ③ demand 그대로. 지금은 건수로 가정한다 (건수인지 가중 합인지 미정: docs/development.md)
- 공고 원문과 근거 문장은 넣지 않는다 (CLAUDE.md 원칙 3, 2026-11-12)
- reason 검사는 코드로 한다: course_id 집합, 숫자, 입력에 없는 영문 이름, 한국어 여부.
  실패하면 converse_json 이 1회 재시도하고, 그래도 실패하면 BedrockJSONError 를 그대로 낸다.
"""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ai.bedrock import SAMPLE_DIR, USAGE_KEYS, ConverseResult, converse_json, is_mock
from ai.prompts import recommend_v1 as prompt
from pipeline.aliases import normalize_skill
from pipeline.search import search_courses

ROOT = Path(__file__).resolve().parent.parent
COURSES_CSV = ROOT / "data" / "courses" / "courses.csv"
MOCK_FILE = "mock_bedrock_recommend.json"
# 가짜 모드에서 기술별 가짜 응답이 없을 때 과정마다 쓰는 문장. 숫자·영문 이름이 없어 reason 검사를 늘 통과한다
MOCK_FALLBACK_REASON = "이 과정은 부족한 역량을 보완하는 데 도움이 되는 내용을 다룹니다."
PROMPT_VERSION = prompt.VERSION
NO_PREREQ = "표기 없음"
# 모든 과정이 AWS Skill Builder 과정이라 reason 에 써도 지어낸 것으로 보지 않는 영문 낱말
ALWAYS_ALLOWED_WORDS = {"aws", "amazon"}

# 글자에 붙은 숫자(EC2, S3)는 빼고 따로 선 숫자만 본다
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9])")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[./-][A-Za-z0-9+#]+)*")


def extract_numbers(text: str) -> set[float]:
    """글 속의 따로 선 숫자 집합(EC2·S3 처럼 글자에 붙은 숫자는 뺀다). LLM 출력의 숫자 검사에 쓴다(#15, #25)."""
    return {float(n) for n in _NUMBER.findall(text)}


_numbers = extract_numbers  # 예전 이름. 다른 브랜치(ai/debate.py 등)가 쓰고 있어 남겨 둔다


@lru_cache(maxsize=1)
def _courses() -> dict[str, dict]:
    with COURSES_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {r["course_id"]: r for r in csv.DictReader(f)}


MATCHES = ("certain", "uncertain")

# match 가 uncertain 인 과정의 reason 끝에 코드가 붙이는 고정 문장(#24). LLM 이 쓴 reason 이 검사를 통과한 뒤에 붙인다.
# ③ 형식은 그대로(reason 문자열에 붙임). 화면의 "불확실" 배지가 왜 붙었는지 설명한다
UNCERTAIN_NOTE = "이 과정이 {skill}을(를) 직접 다루는지는 확실하지 않습니다. 과정 설명에서 {skill}을(를) 직접 다룬다는 내용을 확인하지 못했습니다."
FOUNDATION_NOTE = "{skill}은(는) 범위가 넓어 입문 과정을 붙였습니다. 이 과정이 {skill} 전체를 다루지는 않습니다."
FOUNDATION_FILE = ROOT / "data" / "foundation_courses.json"


@lru_cache(maxsize=1)
def _foundation_ids() -> dict[str, list[str]]:
    """사람이 지정한 넓은 기술의 입문 과정(data/foundation_courses.json)."""
    return json.loads(FOUNDATION_FILE.read_text(encoding="utf-8"))["courses"]


def uncertain_note(skill: str, course: dict) -> Optional[str]:
    """match 가 uncertain 이면 이유를 설명하는 고정 문장, 아니면 None. 지정 입문 과정(AWS 등)은 따로 쓴다."""
    if course.get("match") != "uncertain":
        return None
    canon = normalize_skill(skill) or skill
    if course.get("course_id") in _foundation_ids().get(canon, []):
        return FOUNDATION_NOTE.format(skill=canon)
    return UNCERTAIN_NOTE.format(skill=canon)


RATING_KEYS = ("rating_avg", "rating_count")


def course_rating(course: dict) -> dict:
    """과정 표의 평점 열을 ③ 의 선택 필드로 바꾼다. 열이 없거나 값이 비었으면 빈 dict(2026-11-20).

    화면에 보여 주기만 한다. LLM 입력(llm_input_of)과 추천 순서에는 쓰지 않는다.
    """
    try:
        avg, count = float(course.get("rating_avg") or ""), int(course.get("rating_count") or "")
    except ValueError:
        return {}
    return {"rating_avg": avg, "rating_count": count} if 0 < avg <= 5 and count > 0 else {}


def candidate_courses(skill: str, *, rules_only: bool = False) -> list[dict]:
    """search_courses 결과에서 과정 후보를 고른다. 결과가 과정 데이터와 맞지 않으면 오류를 낸다."""
    if rules_only and is_mock() and not COURSES_CSV.exists():
        demo = json.loads((SAMPLE_DIR / "chat_courses.json").read_text(encoding="utf-8"))
        return demo["courses"].get(skill, [])
    out = []
    links = search_courses(skill, mode="rules") if rules_only else search_courses(skill)
    for link in links:
        cid, sid = link["course_id"], link["subskill_id"]
        course = _courses().get(cid)
        if course is None:
            raise KeyError(f"search_courses 가 준 과정 {cid} 가 courses.csv 에 없습니다")
        if sid is None:
            match = "uncertain"  # 세부 능력 규칙으로 찾은 연결이 아니라 지정한 과정
        else:
            match = link.get("match")
            if match not in MATCHES:
                raise ValueError(f"search_courses 가 준 {cid} 의 match 가 {match!r} 입니다")
        objectives = [o.strip() for o in re.split(r"\s+/\s+|\n", course["objectives"]) if o.strip()]
        if course["retiring"].strip() or not objectives:
            continue
        out.append({
            "course_id": cid,
            "title": course["title"],
            "url": course["url"],
            "level": course["level"],
            "prerequisites": course["prerequisites"].strip() or NO_PREREQ,
            "objectives": objectives,
            "match": match,
            "subskill_id": sid,
            **course_rating(course),
        })
    return out


def llm_input_of(skill: str, demand: dict, courses: list[dict]) -> dict:
    """LLM 에 넣는 값. 비율은 코드가 % 로 바꿔 넣어 LLM 이 계산하지 않게 한다."""
    return {
        "skill": skill,
        "demand": {
            "postings": demand["postings"],
            "of": demand["of"],
            "required_pct": round(demand["required_ratio"] * 100),
        },
        "courses": [
            {k: c[k] for k in ("course_id", "title", "level", "prerequisites", "objectives")}
            for c in courses
        ],
    }


def validate_reasons(data: dict, llm_input: dict) -> None:
    """LLM 출력 검사. 틀리면 ValueError (converse_json 이 한 번 재시도한다)."""
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        raise ValueError('"reasons" 목록이 없습니다')
    for i, r in enumerate(reasons):
        if not (isinstance(r, dict) and isinstance(r.get("course_id"), str)
                and isinstance(r.get("reason"), str) and r["reason"].strip()):
            raise ValueError(f"reasons[{i}] 에 course_id 와 reason 문자열이 필요합니다")
    expected = [c["course_id"] for c in llm_input["courses"]]
    got = [r["course_id"] for r in reasons]
    if sorted(got) != sorted(expected):
        raise ValueError(f"course_id 가 입력과 다릅니다: 입력 {expected}, 응답 {got}")

    d = llm_input["demand"]
    by_id = {c["course_id"]: c for c in llm_input["courses"]}
    for r in reasons:
        c, text = by_id[r["course_id"]], r["reason"]
        source = " ".join([llm_input["skill"], c["title"], c["level"], c["prerequisites"], *c["objectives"]])
        allowed = {float(d["postings"]), float(d["of"]), float(d["required_pct"])} | _numbers(source)
        bad = sorted(_numbers(text) - allowed)
        if bad:
            raise ValueError(
                f"{c['course_id']} reason 에 입력에 없는 숫자 {bad}. "
                f"쓸 수 있는 숫자: 공고 {d['of']}건 중 {d['postings']}건, 필수 {d['required_pct']}%"
            )
        haystack = source.lower()
        unknown = sorted({
            w for w in _LATIN.findall(text)
            if len(w) >= 2 and w.lower() not in ALWAYS_ALLOWED_WORDS and w.lower() not in haystack
        })
        if unknown:
            raise ValueError(f"{c['course_id']} reason 에 입력(objectives 등)에 없는 이름 {unknown}")
        if not re.search(r"[가-힣]", text):
            raise ValueError(f"{c['course_id']} reason 이 한국어가 아닙니다")


@lru_cache(maxsize=1)
def _mock_skills() -> frozenset[str]:
    table = json.loads((SAMPLE_DIR / MOCK_FILE).read_text(encoding="utf-8"))
    return frozenset(k for k in table if k not in ("_note", "default"))


def _mock_fallback(llm_input: dict) -> ConverseResult:
    """가짜 모드에서 기술별 가짜 응답이 없을 때: 받은 과정마다 정해진 문장 하나. 검사는 실제 응답과 똑같이 한다."""
    data = {"reasons": [{"course_id": c["course_id"], "reason": MOCK_FALLBACK_REASON} for c in llm_input["courses"]]}
    validate_reasons(data, llm_input)
    return ConverseResult(data, 1, {k: 0 for k in USAGE_KEYS}, ({"attempt": 1, **{k: 0 for k in USAGE_KEYS}},))


def build_recommendation(
    skill: str, demand: dict, *, review: bool = False,
) -> tuple[dict, Optional[ConverseResult]]:
    """③ 추천 1건과 LLM 호출 결과(시도 횟수·usage)를 돌려준다. 과정이 없으면 LLM 을 부르지 않는다.

    demand 는 ③ 의 demand 형식 {"postings", "of", "required_ratio"} 이다.
    가짜 모드에서 mock_bedrock_recommend.json 에 그 기술의 응답이 없으면 _mock_fallback 을 쓴다
    (화면 연결과 시연을 가짜 모드로 할 때 어떤 기술이 와도 오류가 나지 않게).
    """
    rec = {"gap_skill": skill, "demand": dict(demand), "courses": []}
    courses = candidate_courses(skill, rules_only=True) if review and is_mock() else candidate_courses(skill)
    if not courses:
        return rec, None
    llm_input = llm_input_of(skill, demand, courses)
    if review:
        from ai.recommend_review import reviewed_reasons

        for item, course in zip(llm_input["courses"], courses):
            item["match"] = course["match"]
        result = reviewed_reasons(llm_input)
    elif is_mock() and skill not in _mock_skills():
        result = _mock_fallback(llm_input)
    else:
        result = converse_json(
            prompt.SYSTEM,
            prompt.user_message(llm_input),
            mock_file=MOCK_FILE,
            mock_key=skill,
            validate=lambda data: validate_reasons(data, llm_input),
            max_tokens=2048,
        )
    reasons = {r["course_id"]: r["reason"].strip() for r in result.data["reasons"]}
    rec["courses"] = [
        {
            "title": c["title"],
            "url": c["url"],
            "level": c["level"],
            "prerequisites": c["prerequisites"],
            "reason": " ".join(x for x in (reasons[c["course_id"]], uncertain_note(skill, c)) if x),
            "match": c["match"],
            **{k: c[k] for k in RATING_KEYS if k in c},
        }
        for c in courses if c["course_id"] in reasons
    ]
    return rec, result
