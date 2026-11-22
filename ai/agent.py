"""학습 상담 에이전트 (#25). Converse 도구 호출 반복으로 직접 구현한다(Bedrock Agents 리소스는 만들지 않음).

"Python, Docker 해봤는데 클라우드 엔지니어 되려면 뭐부터 공부해요?" 같은 질문에, 모델이 도구를 골라 부르고
그 결과(코드가 계산한 숫자)로 답한다.

도구 (숫자는 모두 코드가 만든다. 직무는 클라우드 엔지니어 하나)
- get_gap(user_skills): 보유 기술 → 부족 기술 상위 TOP_GAP 개와 공고 수. backend.api.get_gap 을 감싼다.
  표기는 pipeline.aliases.normalize_skill 로만 맞춘다. 사전에 없는 표기는 unrecognized 로 돌려주고 자동으로 바꾸지 않는다.
- get_skill_demand(skill): 공고 n건 중 m건, 그중 필수 비율(%).
- recommend_courses(skills): 부족 기술(최대 MAX_RECOMMEND 개)마다 검증한 과정 추천. 화면 상담과 같은
  backend.api.get_recommendations(..., review=True)(작성 → 독립 검토 → 한 번 수정 → 코드 근거 검사)를 부른다.
  과정마다 제목·난이도·match·reason(검사를 통과한 이유, uncertain 이면 코드가 붙인 안내 문장 포함). 11/19 search_courses 를 대신함

코드가 하는 검사
- 답의 숫자는 이번 대화의 도구 결과와 사용자 메시지(이전 대화 포함)에 있는 것만 허용한다(ai.recommend 의 숫자 검사 방식).
  실패하면 이유를 알려 한 번 다시 묻고, 그래도 실패하면 근거 없는 숫자가 든 문장을 지운다(uncertain_reason "number_check").
- 수요 짝: 답의 "n건 중 m건"(뒤에 "필수 k%" 가 붙은 변형 포함)이 도구 결과의 (전체 공고 수, 그 기술의 공고 수[, 필수 %])
  짝과 맞는지 본다. 숫자가 각각 도구 결과에 있어도 짝이 틀리면("12건 중 4건"을 "4건 중 4건"으로) 걸린다. 처리는 숫자 검사와
  같다(1번 다시 묻고, 그래도 틀리면 그 문장을 지움 → uncertain_reason "pair_check"). 도구 결과에는 코드가 만든 summary
  ("공고 12건 중 4건, 필수 100%")를 넣어 모델이 그대로 옮기게 한다(11/17 실제 실행에서 짝을 잘못 지은 답이 나옴).
- 마지막 답은 answer 도구({"answer", "scope"})로 받는다. answer 가 불리면 반복이 끝난다. 다른 도구와 같은 차례에 불리면
  다른 도구를 먼저 실행하고 answer 에는 "결과를 보고 다시 부르라"는 오류를 돌려줘 다시 묻는다(ai.bedrock.converse_tools).
  안전장치: answer 없이 글로 답하면 그 글을 JSON 으로 읽고, 아니면 형식 재질문. 두 번 모두 실패하면 BedrockJSONError(CLAUDE.md 코드 규칙).
- unrecognized 표기가 있는데 답에 되묻기가 없으면 코드가 "OO는 XX를 말씀하신 건가요?" 를 덧붙인다.
- 취업 준비 질문(scope "in")인데 도구를 한 번도 성공적으로 부르지 않았으면 uncertain_reason "no_tool".
- 도구만 부르다 MAX_TURNS 에 걸리면 정해진 안내와 uncertain_reason "max_turns".
- 가드레일(#24): 입력은 guard_input, 답은 guard_output 이 ai.guardrail 을 부른다. 입력이 막히면 도구·모델을 부르지 않고
  고정 문장을 돌려준다. 답이 막히면 답 대신 고정 문장, uncertain_reason "guardrail".

명령 (저장소 루트에서)
  python -m ai.agent --warmup        시연 전 준비. 사전 이름 임베딩 캐시를 만든다(실제 모드). 호출 수와 걸린 시간을 출력
  python -m ai.agent "질문"           한 번 물어보고 결과를 JSON 으로 출력

기록: 대화 1건마다 도구 순서·횟수, 모델 호출 수, 재질문 여부와 이유(number / pair / format), 검사에 걸린 숫자와
틀린 수요 짝(숫자만), 토큰, 응답 시간을 로그에 남긴다. 불확실 사유는 no_tool / max_turns / number_check / pair_check / guardrail.
사용자 메시지와 답 원문, 도구 입력(보유 기술)은 로그에 남기지 않는다(CLAUDE.md 보안: 대화 기록은 로그에 출력하지 않음).
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from contextvars import ContextVar
from typing import Optional

from ai.bedrock import BedrockJSONError, SAMPLE_DIR, USAGE_KEYS, add_usage, converse_tools, is_mock, parse_json_object
from ai import guardrail
from ai.prompts import agent_v1 as prompt
from ai.recommend import extract_numbers as _numbers
from backend import api
from pipeline.aliases import _index, _key, normalize_skill, normalize_skills

JOB = "cloud_engineer"
TOP_GAP = 3          # get_gap 이 돌려주는 부족 기술 수 (도구 결과가 대화에 쌓여 토큰이 늘어 5 → 3, 11/17)
TOP_COURSES = 3      # recommend_courses 가 기술마다 돌려주는 과정 수
MAX_RECOMMEND = 3    # recommend_courses 한 번에 추천하는 기술 수(기술마다 모델 호출 2~3번)
NO_DEMAND_SUMMARY = "수요 집계에 없는 기술(공고 수 정보 없음)"
NO_DEMAND_REASON = "이 기술은 공고 수요 집계에 없어 공고 수 정보는 없습니다."
_MESSAGE: ContextVar[str] = ContextVar("agent_message", default="")  # 이번 사용자 메시지(이름 댄 기술 판정용)
# 화면용(backend/agent_screen.py): 프로필·사이드바 보유 기술. get_gap 입력에 코드가 합친다(프롬프트는 그대로). 평가 경로는 빈 값
_BASE_SKILLS: ContextVar[tuple] = ContextVar("agent_base_skills", default=())
# 화면용: recommend_courses 가 받은 과정 원본(카드에 필요한 url·언어 등). 모델에게는 4칸만 준다
_COURSE_ROWS: ContextVar[Optional[list]] = ContextVar("agent_course_rows", default=None)
MAX_TURNS = 4        # 도구 호출 반복 한도(모델 호출 수)
MAX_SKILLS = 30      # get_gap 입력 기술 수 한도
CLOSE_MIN = 0.5      # 문자열 비교 후보의 최소 비슷도 (도카↔도커 0.5, 파이선↔파이썬 0.67)
MAX_SUGGEST = 5      # get_gap 한 번에 maybe 후보를 찾는 표기 수. 실제 모드는 표기마다 임베딩 호출이 들어서 나머지는 null
MOCK_FILE = "mock_bedrock_agent.json"
PROMPT_VERSION = prompt.VERSION

MAX_TURNS_ANSWER = "질문을 정리하는 데 시간이 걸려 답을 마치지 못했습니다. 해 본 기술과 궁금한 점을 조금 더 짧게 알려 주세요."
NO_NUMBER_ANSWER = "근거를 확인할 수 있는 숫자 없이 답을 정리하지 못했습니다. 다시 한번 물어봐 주세요."
ASK_BACK = "'{input}'은(는) '{maybe}'을(를) 말씀하신 건가요?"
ASK_BACK_UNKNOWN = "'{input}'은(는) 아직 사전에 없는 기술이라 부족 기술 계산에 넣지 않았습니다. 다른 이름으로 알려 주시겠어요?"
ASK_BACK_MARK = "말씀하신 건가요"
SKIPPED_TOOL = "이 호출은 처리하지 않았습니다. 다른 도구는 부르지 말고 answer 로만 답을 다시 내세요."
PAIR_ERROR ="답의 '건 중 건' 수요가 도구 결과와 짝이 맞지 않습니다: {pairs}. 수요를 말할 때는 도구 결과의 summary 를 그대로 옮기세요"

# 목록 번호는 숫자 검사에서 뺀다. 줄 맨 앞의 "1. ", 꾸민 것("**1.", "### 1.", "- 1)", "> **2.**")까지.
# 번호 뒤에는 빈칸·꾸밈 기호·줄 끝만 온다("12.5%"·"12건" 같은 줄 맨 앞 숫자는 그대로 검사한다)
_LIST_MARK = re.compile(r"(?m)^[ \t]*(?:[#>]+[ \t]*|[-*+][ \t]+)*(?:\*\*|__)?\d+[.)](?=[ \t*_]|$)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

logger = logging.getLogger(__name__)


# ── 도구 ────────────────────────────────────────────────────────────
def maybe_skill(raw: str) -> Optional[str]:
    """사전에 없는 표기의 대표명 후보 하나(되묻기용). 바꾸지 않고 묻기만 한다.

    실제 모드는 pipeline.suggest(임베딩)에서 confidence 가 candidate 인 1위만 쓴다. 가짜 모드이거나 suggest 가
    실패하면 사전 표기와의 문자열 비교(difflib)로 찾는다. 둘 다 없으면 None.
    """
    if not is_mock():
        try:
            from pipeline.suggest import suggest

            top = suggest(raw, top=1)
            return top[0]["canonical"] if top and top[0]["confidence"] == "candidate" else None
        except Exception as e:  # noqa: BLE001 - 후보는 되묻기용이라 실패해도 문자열 비교로 넘어간다
            logger.warning("pipeline.suggest 실패, 문자열 비교로 후보를 찾습니다: %s", type(e).__name__)
    return _close_name(raw)


def _close_name(raw: str) -> Optional[str]:
    """문자열 비교 후보. 비슷도 하나로는 가를 수 없어(자바스크립트↔셸스크립트 0.73 > 도카↔도커 0.5) 조건을 셋 둔다.

    1) 첫 글자가 같은 사전 표기만 본다. 2) 비슷도(difflib ratio)가 CLOSE_MIN 이상. 3) 사전 표기 뒤에 두 글자 이상이
    더 붙은 표기는 다른 기술일 수 있어 후보로 내지 않는다(Java ≠ JavaScript, 자바 ≠ 자바스크립트). 못 찾으면 None.
    """
    key = _key(raw)
    if not key:
        return None
    index = _index()
    best, best_score = None, CLOSE_MIN
    for name in index:
        if name[0] != key[0] or (key.startswith(name) and len(key) - len(name) >= 2):
            continue
        score = difflib.SequenceMatcher(None, key, name).ratio()
        if score >= best_score and (best is None or score > best_score):
            best, best_score = name, score
    return index[best] if best else None


def _pct(part: int, whole: int) -> int:
    return round(part / whole * 100) if whole else 0


def demand_summary(of: int, postings: int, required_pct: int) -> str:
    """수요를 말하는 문장 조각. 코드가 만들고, 모델은 그대로 옮긴다(숫자 짝을 잘못 짓지 않게, 11/17)."""
    return f"공고 {of}건 중 {postings}건, 필수 {required_pct}%"


def tool_get_gap(user_skills: list) -> dict:
    if not isinstance(user_skills, list):
        raise ValueError("user_skills 는 목록이어야 합니다")
    said = [str(s).strip() for s in user_skills if str(s).strip()]
    said += [s for s in _BASE_SKILLS.get() if s not in said]  # 화면용: 프로필·사이드바 보유 기술을 코드가 합친다
    known, unknown = normalize_skills(said[:MAX_SKILLS])
    demand = api.get_demand(JOB)
    total = demand["total_postings"]
    raw = {s["skill"]: s for s in demand["skills"]}  # 필수 비율은 get_skill_demand 와 같은 _pct 로(반올림을 맞춘다)
    rows = api.get_gap(JOB, known)
    # 답에 필요한 칸만 둔다(공고 수 of 는 위에 한 번만). 도구 결과는 대화에 쌓여 다음 호출마다 입력 토큰이 된다
    return {
        "of": total,
        "basis": "recognized_skills" if known else "no_skills",  # 부족 기술을 무엇 기준으로 셌는지
        "recognized_skills": known,
        "unrecognized": [{"input": u, "maybe": maybe_skill(u) if i < MAX_SUGGEST else None}
                         for i, u in enumerate(unknown)],
        "missing_top": [
            {"skill": r["skill"], "postings": r["postings"], "required_pct": pct,
             "summary": demand_summary(total, r["postings"], pct)}
            for r in rows if not r["owned"]
            for pct in [_pct(raw[r["skill"]]["required"], raw[r["skill"]]["postings"])]
        ][:TOP_GAP],
    }


def tool_get_skill_demand(skill: str) -> dict:
    canon = normalize_skill(skill)
    demand = api.get_demand(JOB)
    total = demand["total_postings"]
    name = (canon or skill).lower()
    s = next((x for x in demand["skills"] if x["skill"].lower() == name), None)
    if s is None:
        return {"skill": canon or skill, "found": False, "of": total,
                "maybe": None if canon else maybe_skill(skill), "summary": "수요 집계에 없는 기술"}
    pct = _pct(s["required"], s["postings"])
    return {"skill": s["skill"], "found": True, "postings": s["postings"], "of": total,
            "required_pct": pct, "summary": demand_summary(total, s["postings"], pct)}


def _named_skills() -> set[str]:
    """이번 사용자 메시지에 실제로 나온 기술(대표명). 화면 해석과 같은 ai.chat.mentioned_skills 를 쓴다(읽기만)."""
    from ai.chat import mentioned_skills

    return {x.lower() for x in mentioned_skills(_MESSAGE.get()) if x}


def _drop_pair_sentences(reason: str) -> str:
    """수요 집계에 없는 기술의 이유에서 "n건 중 m건" 문장을 뺀다(공고 수 0 으로 넘어가 "0건에서 언급"이 되므로)."""
    kept = [x for x in re.split(r"(?<=[.!?。])\s+", reason) if x.strip() and not _PAIR.search(x)]
    return " ".join([NO_DEMAND_REASON, *kept])


def tool_recommend_courses(skills: list) -> dict:
    """부족 기술마다 검증한 과정 추천(화면 상담과 같은 api.get_recommendations(review=True)).

    표기는 별칭 사전으로만 맞춘다. 수요 집계에 있는 기술은 공고 수·필수 비율·summary 를 get_gap 과 같은 계산으로 준다.
    수요 집계에 없는 기술은 사용자가 이번 메시지에서 직접 이름을 댔을 때만 추천하고(수요는 "정보 없음"), 아니면 not_found.
    (화면 상담 구조 0 은 이름 댄 기술 대신 Gap 의 상위 부족 기술을 추천한다: backend/chat.py _analyze. 11/19 결정 ⓐ)
    reason 은 검사를 통과한 문장이다. 수요 정보가 없는 기술은 공고 수를 말하는 문장만 빼고 안내 문장을 앞에 붙인다.
    """
    if not isinstance(skills, list):
        raise ValueError("skills 는 목록이어야 합니다")
    demand = api.get_demand(JOB)
    total = demand["total_postings"]
    raw = {s["skill"].lower(): s for s in demand["skills"]}
    named = _named_skills()
    picked, not_found = [], []
    for name in [str(x).strip() for x in skills if str(x).strip()]:
        canon = normalize_skill(name) or name
        row = raw.get(canon.lower())
        skill = row["skill"] if row else canon
        if row is None and canon.lower() not in named:
            not_found.append(canon)
        elif skill not in picked:
            picked.append(skill)
    picked = picked[:MAX_RECOMMEND]
    recs = api.get_recommendations(JOB, picked, review=True) if picked else []
    out = []
    for rec in recs:
        s = raw.get(rec["gap_skill"].lower())
        courses = [{k: c[k] for k in ("title", "level", "match", "reason")} for c in rec["courses"][:TOP_COURSES]]
        if s is None:  # 사용자가 이름을 댄, 수요 집계에 없는 기술
            for c in courses:
                c["reason"] = _drop_pair_sentences(c["reason"])
        rows = _COURSE_ROWS.get()
        if rows is not None:  # 화면용: 원본 과정에 모델에게 준 것과 같은 reason 을 붙여 둔다
            rows.append({"gap_skill": rec["gap_skill"],
                         "courses": [{**full, "reason": c["reason"]} for full, c in zip(rec["courses"], courses)]})
        if s is None:
            out.append({"skill": rec["gap_skill"], "demand_known": False, "summary": NO_DEMAND_SUMMARY, "courses": courses})
            continue
        pct = _pct(s["required"], s["postings"])
        out.append({
            "skill": rec["gap_skill"], "demand_known": True, "postings": s["postings"], "required_pct": pct,
            "summary": demand_summary(total, s["postings"], pct), "courses": courses,
        })
    return {"of": total, "recommendations": out, "not_found": not_found}


TOOL_FUNCS = {
    "get_gap": lambda inp: tool_get_gap(inp.get("user_skills", [])),
    "get_skill_demand": lambda inp: tool_get_skill_demand(str(inp.get("skill", ""))),
    "recommend_courses": lambda inp: tool_recommend_courses(inp.get("skills", [])),
}


# ── 검사 ────────────────────────────────────────────────────────────
def guard_input(message: str) -> Optional[str]:
    """가드레일(#24). 막아야 하면 사용자에게 보여줄 고정 안내 문장, 통과면 None(ai.guardrail.check_input).
    Guardrails 호출이 실패해도 막고 "안전 검사를 할 수 없어요" 안내를 보여준다."""
    result = guardrail.check_input(message)
    return guardrail.message_for(result, guardrail.BLOCK_MESSAGE) if result.blocked else None


def guard_output(answer: str) -> Optional[str]:
    """답 검사(#24). GUARDRAIL_OUTPUT=1 일 때 Bedrock Guardrails 만(코드 검사는 질문에만). 막히면(호출 실패 포함) 고정 문장."""
    result = guardrail.check_output(answer)
    return guardrail.message_for(result, guardrail.OUTPUT_BLOCK_MESSAGE) if result.blocked else None


def _walk_numbers(value) -> set[float]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, str):
        return _numbers(value)
    if isinstance(value, dict):
        return set().union(*(_walk_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_walk_numbers(v) for v in value)) if value else set()
    return set()


def answer_numbers(answer: str) -> set[float]:
    return _numbers(_LIST_MARK.sub("", answer))


def check_answer(raw, allowed: set[float]) -> tuple[Optional[dict], list[float], Optional[str]]:
    """(읽은 답, 근거 없는 숫자, 오류 설명). 오류가 없으면 설명이 None.

    raw 는 answer 도구 입력(dict) 이거나, 모델이 도구 없이 글로 답한 문자열(JSON 이어야 함, 안전장치).
    """
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = parse_json_object(raw)
        except ValueError as e:
            return None, [], f"answer 도구를 부르지 않았고 글도 JSON 객체가 아닙니다({type(e).__name__})"
    if not isinstance(data.get("answer"), str) or not data["answer"].strip():
        return None, [], '"answer" 가 비었거나 문자열이 아닙니다'
    if data.get("scope") not in ("in", "out"):
        return None, [], '"scope" 는 "in" 또는 "out" 이어야 합니다'
    bad = sorted(answer_numbers(data["answer"]) - allowed)
    if bad:
        shown = ", ".join(f"{b:g}" for b in bad)
        return data, bad, f"답에 도구 결과에 없는 숫자가 있습니다: {shown}. 도구 결과에 있는 숫자만 그대로 쓰세요"
    return data, [], None


# "공고 12건 중 4건", "12건 가운데 4건(필수 100%)", "12건 중 4건, 필수 100%", "12건 중 4건, 그중 필수 비율 100%"
_PAIR = re.compile(
    r"(\d+)\s*건\s*(?:중|가운데)\s*(\d+)\s*건"
    r"(?:\s*[(,·]?\s*(?:그중\s*)?필수(?:\s*비율)?\s*(?:은|는|이)?\s*(\d+)\s*%)?"
)


def demand_facts(outputs: list[dict], texts: list[str]) -> dict:
    """맞는 수요 짝. 도구 결과의 (전체 공고 수, 그 기술의 공고 수)와 (…, 필수 %), 그리고 사용자·이전 대화에 이미 나온 짝."""
    pairs, triples = set(), set()

    def add(of, postings, pct=None):
        pairs.add((int(of), int(postings)))
        if pct is not None:
            triples.add((int(of), int(postings), int(pct)))

    for out in outputs:
        for row in out.get("missing_top", []):
            add(out["of"], row["postings"], row.get("required_pct"))
        if out.get("found"):
            add(out["of"], out["postings"], out.get("required_pct"))
        for row in out.get("recommendations", []):
            if row.get("demand_known"):  # 수요 정보가 없는 기술은 맞는 짝이 없다
                add(out["of"], row["postings"], row.get("required_pct"))
    for text in texts:
        for n, m, k in _PAIR.findall(text):
            add(n, m, k or None)
    return {"pairs": pairs, "triples": triples}


def check_pairs(answer: str, facts: dict) -> list[str]:
    """답의 "n건 중 m건"(필수 k% 포함)이 맞는 짝인지. 틀린 짝을 "(n, m)" / "(n, m, k%)" 로 돌려준다(숫자만)."""
    wrong = []
    for n, m, k in _PAIR.findall(answer):
        if (int(n), int(m)) not in facts["pairs"]:
            wrong.append(f"({n}, {m})")
        elif k and (int(n), int(m), int(k)) not in facts["triples"]:
            wrong.append(f"({n}, {m}, {k}%)")
    return wrong


def strip_numbers(answer: str, allowed: set[float], facts: Optional[dict] = None) -> str:
    """근거 없는 숫자나 틀린 수요 짝이 든 문장을 지운다(줄 단위로 나눈 뒤 문장 단위)."""
    lines = []
    for line in answer.splitlines():
        kept = [s for s in _SENTENCE.split(line)
                if not (answer_numbers(s) - allowed) and not (facts and check_pairs(s, facts))]
        if " ".join(kept).strip():
            lines.append(" ".join(kept))
    return "\n".join(lines).strip()


# ── 대화 ────────────────────────────────────────────────────────────
def _history_messages(history: Optional[list]) -> list:
    out = []
    for i, turn in enumerate(history or []):
        role = turn.get("role") if isinstance(turn, dict) else None
        want = "user" if i % 2 == 0 else "assistant"
        if role != want or not isinstance(turn.get("content"), str):
            raise ValueError(f"history[{i}] 는 role {want!r} 와 문자열 content 여야 합니다(user 부터 번갈아)")
        out.append({"role": role, "content": [{"text": turn["content"]}]})
    if out and out[-1]["role"] != "assistant":
        raise ValueError("history 는 assistant 답으로 끝나야 합니다")
    return out


def _mock_key(message: str) -> str:
    """가짜 모드에서 쓸 대본: mock 파일 _when 의 낱말이 메시지에 있으면 그 대본, 없으면 default."""
    table = json.loads((SAMPLE_DIR / MOCK_FILE).read_text(encoding="utf-8"))
    for key, words in table.get("_when", {}).items():
        if any(w in message for w in words):
            return key
    return "default"


def _final_answer(loop):
    """반복 결과에서 검사할 답: answer 도구 입력, 없으면 글(안전장치). 도구만 부르다 끝났으면 None."""
    if loop.stop == "final_tool":
        return loop.final["input"]
    return loop.text if loop.stop == "end_turn" else None


def _retry_turn(loop, error: str) -> dict:
    """다시 물을 때 보낼 user 메시지. 직전 답이 answer 도구면 그 호출의 toolResult(오류)로 돌려준다(Converse 규칙)."""
    note = prompt.RETRY_MESSAGE.format(error=error)
    if loop.stop != "final_tool":
        return {"role": "user", "content": [{"text": note}]}
    # Converse 규칙: 마지막 assistant 메시지의 toolUse 하나하나에 toolResult 를 돌려준다(빠지면 요청이 거부된다)
    last = loop.messages[-1]["content"] if loop.messages else []
    ids = [b["toolUse"]["toolUseId"] for b in last if "toolUse" in b] or [loop.final["id"]]
    return {"role": "user", "content": [
        {"toolResult": {"toolUseId": i, "status": "error",
                        "content": [{"text": note if i == loop.final["id"] else SKIPPED_TOOL}]}}
        for i in ids
    ]}


def _result(answer: str, calls: list, usage: dict, started: float, uncertain_reason: Optional[str]) -> dict:
    return {
        "answer": answer,
        "tool_calls": calls,
        "usage": usage,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "uncertain": uncertain_reason is not None,
        "uncertain_reason": uncertain_reason,
    }


def chat(message: str, history: Optional[list] = None, base_skills: Optional[list] = None) -> dict:
    """화면용 진입점. 이번 메시지를 추천 도구가 읽을 수 있게 둔 채 _chat 을 부른다(이름 댄 기술 판정).

    base_skills(화면용): 프로필·사이드바 보유 기술. get_gap 입력에 코드가 합친다. 주면 결과에 course_rows(과정 원본)도 담는다.
    결과의 guard 는 입력 검사 결과("pass", "blocked", "check_failed"), scope 는 답 범위("in", "out", 막히면 None).
    """
    tokens = (_MESSAGE.set((message or "").strip()),
              _BASE_SKILLS.set(tuple(dict.fromkeys(str(s).strip() for s in base_skills or [] if str(s).strip()))),
              _COURSE_ROWS.set([]))
    try:
        result = _chat(message, history)
        result["course_rows"] = _COURSE_ROWS.get()
        return result
    finally:
        _COURSE_ROWS.reset(tokens[2])
        _BASE_SKILLS.reset(tokens[1])
        _MESSAGE.reset(tokens[0])


def _chat(message: str, history: Optional[list] = None) -> dict:
    """화면용. {"answer", "tool_calls", "usage", "latency_ms", "uncertain", "uncertain_reason"}.

    history 는 [{"role": "user"|"assistant", "content": str}] (user 부터 번갈아, assistant 로 끝남).
    tool_calls 는 호출 순서대로 {"name", "input", "ok"}.
    """
    started = time.monotonic()
    message = (message or "").strip()
    if not message:
        raise ValueError("message 가 비었습니다")
    usage = {k: 0 for k in USAGE_KEYS}
    checked = guardrail.check_input(message)  # guard_input 과 같은 검사. 화면용으로 막힘과 검사 실패를 나눠 돌려준다
    if checked.blocked:
        return {**_result(guardrail.message_for(checked, guardrail.BLOCK_MESSAGE), [], usage, started, None),
                "guard": "check_failed" if guardrail.is_check_failure(checked) else "blocked", "scope": None}

    msgs = _history_messages(history) + [{"role": "user", "content": [{"text": message}]}]
    outputs: list[dict] = []

    def run_tool(name: str, inp: dict) -> dict:
        if name not in TOOL_FUNCS:
            raise ValueError(f"없는 도구: {name}")
        out = TOOL_FUNCS[name](inp)
        outputs.append(out)
        return out

    key = _mock_key(message) if is_mock() else "real"
    tool_args = dict(mock_file=MOCK_FILE, mock_key=key, final_tool=prompt.FINAL_TOOL)
    loop = converse_tools(prompt.SYSTEM, msgs, prompt.TOOLS, run_tool, max_turns=MAX_TURNS, **tool_args)
    add_usage(usage, loop.usage)
    calls, model_calls = list(loop.tool_calls), loop.model_calls

    reason = None
    retried = None  # 재질문 이유: None(없음), "number"(숫자), "pair"(n건 중 m건 짝), "format"(answer 도구 없이 JSON 아닌 글 등). 로그에만
    bad_numbers: dict[str, list[float]] = {}  # 검사에 걸린 숫자만(원문 없이) 로그에 남긴다. {"1차": [...], "2차": [...]}
    wrong_pairs: dict[str, list[str]] = {}    # 틀린 수요 짝만(숫자만) 로그에 남긴다. {"1차": ["(4, 4)"]}
    via = {"final_tool": "answer도구", "end_turn": "글"}.get(loop.stop, "-")  # 마지막 답을 어떻게 받았는지(로그)
    if loop.stop == "max_turns":
        answer, scope, reason = MAX_TURNS_ANSWER, "in", "max_turns"
    else:
        said = [message, *((h or {}).get("content", "") for h in (history or []))]
        allowed = _walk_numbers(outputs) | set().union(*(_numbers(t) for t in said))
        data, bad, error = check_answer(_final_answer(loop), allowed)
        pairs_bad = check_pairs(data["answer"], demand_facts(outputs, said)) if error is None else []
        if bad:
            bad_numbers["1차"] = bad
        if pairs_bad:
            wrong_pairs["1차"] = pairs_bad
            error = PAIR_ERROR.format(pairs=", ".join(pairs_bad))
        if error is not None:
            retried = "number" if bad else "pair" if pairs_bad else "format"
            retry = converse_tools(prompt.SYSTEM, loop.messages + [_retry_turn(loop, error)], prompt.TOOLS, run_tool,
                                   max_turns=1, mock_turn_start=model_calls + 1, attempt=2, **tool_args)
            add_usage(usage, retry.usage)
            calls += list(retry.tool_calls)
            model_calls += retry.model_calls
            allowed |= _walk_numbers(outputs)
            facts = demand_facts(outputs, said)
            raw2 = _final_answer(retry)
            data2, bad2, error2 = check_answer(raw2, allowed) if raw2 is not None else (None, [], "도구를 다시 불렀습니다")
            pairs_bad2 = check_pairs(data2["answer"], facts) if error2 is None else []
            via = {"final_tool": "answer도구", "end_turn": "글"}.get(retry.stop, "-")
            if bad2:
                bad_numbers["2차"] = bad2
            if pairs_bad2:
                wrong_pairs["2차"] = pairs_bad2
                error2 = PAIR_ERROR.format(pairs=", ".join(pairs_bad2))
            if error2 is None:
                data = data2
            else:
                kept_bad = (bad2, pairs_bad2) if data2 is not None else (bad, pairs_bad)
                data = data2 if data2 is not None else data
                if data is None:
                    raise BedrockJSONError(f"상담 답이 두 번 모두 형식 검사를 통과하지 못했습니다: {error} / {error2}", usage)
                data = {**data, "answer": strip_numbers(data["answer"], allowed, facts) or NO_NUMBER_ANSWER}
                reason = "pair_check" if kept_bad[1] and not kept_bad[0] else "number_check"
        answer, scope = data["answer"].strip(), data["scope"]

        unknown = [u for out in outputs for u in out.get("unrecognized", [])]
        if unknown and ASK_BACK_MARK not in answer:
            asks = [ASK_BACK.format(**u) if u["maybe"] else ASK_BACK_UNKNOWN.format(input=u["input"]) for u in unknown]
            answer = answer + "\n\n" + " ".join(dict.fromkeys(asks))
        if reason is None and scope == "in" and not any(c["ok"] for c in calls):
            reason = "no_tool"
        blocked_answer = guard_output(answer)
        if blocked_answer is not None:  # 답이 가드레일에 걸리면 답 대신 고정 문장
            answer, reason = blocked_answer, "guardrail"

    result = {**_result(answer, calls, usage, started, reason), "guard": "pass",
              "scope": scope}
    logger.info(
        "상담 1건: 도구 %s, 도구 %d회, 모델 %d회, 답 %s, 재질문 %s, 근거 없는 숫자 %s, 짝 틀림 %s, 토큰 %s, %dms, 불확실 %s (prompt=%s)",
        [c["name"] for c in calls], len(calls), model_calls, via, retried or "없음",
        {k: [f"{n:g}" for n in v] for k, v in bad_numbers.items()} or "없음", wrong_pairs or "없음",
        usage.get("totalTokens", 0), result["latency_ms"], reason, PROMPT_VERSION,
    )
    return result


# ── 시연 전 준비와 한 번 물어보기 ─────────────────────────────────────
def warmup() -> int:
    """pipeline.suggest 가 쓰는 사전 이름 임베딩을 모두 캐시(data/raw/embed_cache.json)에 만든다. 실제 모드에서만.

    캐시가 없으면 첫 되묻기 질문에서 사전 이름 200개를 임베딩하느라 40초 넘게 걸린다(11/17 실측 39.5초).
    이미 캐시에 있는 이름은 다시 부르지 않으므로 여러 번 돌려도 된다.
    """
    from pipeline import embed
    from pipeline.suggest import suggest

    if embed.is_mock():  # .env 를 읽어 BEDROCK_MOCK·EMBED_MODEL_ID 를 올린다
        print("가짜 모드(BEDROCK_MOCK=1)라 임베딩을 부르지 않습니다. BEDROCK_MOCK=0 과 EMBED_MODEL_ID 를 설정한 뒤 다시 실행하세요.")
        return 1
    before, started = embed.usage()["calls"], time.monotonic()
    top = suggest("도커", top=1)  # 사전 이름 전체와 질의 1개를 임베딩한다
    calls = embed.usage()["calls"] - before
    print(f"임베딩 호출 {calls}번, {time.monotonic() - started:.1f}초. 캐시: {embed.CACHE_FILE.name} (data/raw/, 커밋하지 않음)")
    print(f"확인: '도커' → {top[0]['canonical']} (점수 {top[0]['score']})")
    return 0


def main() -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="학습 상담 에이전트 (#25)")
    ap.add_argument("message", nargs="?", help="한 번 물어볼 질문. 결과(답, 도구, 토큰, 시간)를 JSON 으로 출력한다")
    ap.add_argument("--warmup", action="store_true", help="시연 전 준비: 사전 이름 임베딩 캐시를 만든다(실제 모드)")
    args = ap.parse_args()
    if args.warmup:
        return warmup()
    if args.message:
        print(json.dumps(chat(args.message), ensure_ascii=False, indent=1))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(SAMPLE_DIR.parent.parent / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
    except ImportError:
        pass
    raise SystemExit(main())
