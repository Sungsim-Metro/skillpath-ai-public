"""상담 구조 비교(#93·#94). 같은 질문 세트를 구조마다 한 번씩 돌려 코드 지표를 센다. 계산은 전부 코드로 한다.

실행 (저장소 루트에서)
  python -m ai.eval.agent_compare                       개선용 문항(data/eval/agent_cases.json), 구조 0, 가짜 모드
  python -m ai.eval.agent_compare --structures 0 A      구조 0(화면 경로)과 A(상담 에이전트, 아직 없음)
  python -m ai.eval.agent_compare --bedrock             실제 모드(BEDROCK_MOCK=0 과 모델·가드레일·DynamoDB 설정 필요)
  python -m ai.eval.agent_compare --set holdout         보지 않은 문항. 숫자만 출력(문항 id·원문 없음). 채점 직전에 한 번만

구조
- 0 화면 경로: backend.chat.handle_message(가드레일 → 발화 해석 → 코드 분기 → Gap 또는 검토 추천). 지금 화면이 쓰는 경로
- A 상담 에이전트 + 추천 도구: ai.agent.chat(도구 호출 반복, 과정은 recommend_courses = 화면과 같은 검증 추천)
- 세 구조 모두 입력 검사는 ai.guardrail.check_input 하나다. 가드레일 문항(regression)은 비교 점수에서 빼고 결과가 같은지만 본다

지표 (문항마다 기록하고 코드로 합친다)
- 의도 정확도: 관찰한 의도(gap·demand·courses·out_of_scope·blocked 등)가 expect_intent 와 같은 비율(가드레일·참고 문항 제외).
  그 구조에 없는 의도를 기대하는 문항(구조 0 의 demand)은 "해당 없음"으로 따로 세고 분모에서 뺀다
- 되묻기 정확도: 표기 애매 문항에서 되묻기 여부가 expect_ask_back 과 같은 비율(답 문장의 되묻기 표현으로 판정).
  기술 표기를 되묻는 기능이 없는 구조(구조 0)는 "해당 없음"으로 따로 세고 분모에서 뺀다
- 가드레일: 정책상 차단·통과·검사 실패를 따로 센다. 검사 실패를 차단으로 세지 않는다
- 불확실 비율: 보여 준 과정 중 match 가 uncertain 인 비율
- 모델 호출 수(Guardrails 호출은 포함하지 않음), 토큰, 시간(중앙값·최대). 두 구조 모두 모델 호출 응답의 토큰을 직접 더한다(가짜 모드는 0, 가짜 대본 차례는 mock_turns)
결과는 outputs/eval/agent_compare_*.json 에 저장한다(.gitignore 대상).

캐시: 추천(backend.api 의 추천 캐시)·수요(60초 캐시) 같은 메모리 캐시가 구조·문항 사이에 남으면 뒤에 도는 쪽의 호출 수와
시간이 줄어 비교가 틀어진다. backend 에 캐시를 비우는 공개 함수가 없어(내부 변수는 건드리지 않는다), (구조, 문항)마다
이 스크립트를 새 프로세스(--one)로 띄워 늘 빈 캐시에서 시작한다. 그만큼 DynamoDB 수요 조회가 문항마다 한 번씩 더 일어난다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
except ImportError:
    pass

from ai import bedrock, guardrail  # noqa: E402
from ai import chat as intake  # noqa: E402

EVAL_DATA = ROOT / "data" / "eval"  # 공개 범위 밖
CASES = EVAL_DATA / "agent_cases.json"
HOLDOUT = EVAL_DATA / "agent_cases_holdout.json"
OUT_DIR = ROOT / "outputs" / "eval"
INTENTS = ("gap", "demand", "courses", "out_of_scope", "blocked")
# 구조마다 고를 수 있는 의도. 없는 의도를 기대하는 문항은 "해당 없음"으로 따로 세고 의도 정확도 분모에서 뺀다.
# 구조 0(화면 경로)의 해석 action 에는 "기술 하나 수요"(demand)가 없다(gap·courses·note·summary)
SUPPORTED_INTENTS = {"0": {"gap", "courses", "out_of_scope", "blocked"}}
# 사전에 없는 기술 표기를 되묻는 기능이 있는 구조. 구조 0(화면 경로)은 없다: backend/chat.py 의 _skill 이 모르는 표기를
# 그대로 두고, ai/chat.py 의 status(clarify_role·incomplete)는 직무·미완성 입력 확인이지 기술 표기 되묻기가 아니다(11/19 확인).
# 없는 구조는 표기 애매 문항의 되묻기를 "해당 없음"으로 따로 세고 분모에서 뺀다
ASK_BACK_STRUCTURES = {"A", "B"}
RESULT_NOTES = [
    "모델 호출 수에 Guardrails(ApplyGuardrail) 호출은 포함하지 않음",
    "구조·문항마다 새 프로세스로 실행해 캐시가 비어 있음, DynamoDB 수요 조회가 문항마다 한 번씩 더 일어남",
    "KB Retrieve(과정 검색)·임베딩 호출은 상한 없이 개수만 기록함(kb_retrieve_calls, embedding_calls)",
    "구조 0은 이름 댄 기술 대신 상위 부족 기술을 추천함(backend/chat.py _analyze). 구조 A 는 이름 댄 기술을 먼저 추천함(수요에 없으면 공고 수 정보 없음)",
]
ROW_MARK = "AGENT_COMPARE_ROW "  # 하위 프로세스가 결과 한 줄 앞에 붙이는 표시(다른 출력과 섞이지 않게)
CHILD_TIMEOUT = 600                # 문항 하나의 하위 프로세스 시간 한도(초)
# 호출 한도: 부모가 남은 한도를 하위 프로세스에 환경 변수로 넘기고, 하위 프로세스는 호출 직전에 한도를 넘으면 멈춘다
MODEL_BUDGET_ENV = "AGENT_COMPARE_MODEL_BUDGET"
GUARDRAIL_BUDGET_ENV = "AGENT_COMPARE_GUARDRAIL_BUDGET"
LIMIT_EXIT = 3                     # 한도에 걸려 멈춘 하위 프로세스의 종료 코드


class BudgetExceeded(BaseException):
    """호출 한도를 넘기 직전. BaseException 이라 상담 코드의 except Exception 이 삼키지 않고 바로 멈춘다."""


class LimitReached(RuntimeError):
    """부모: 호출 한도에 걸려 측정을 멈춤(그때까지의 결과만 저장)."""
# 답에 되묻기가 있는지(표기 애매 문항). 상담 에이전트의 보충 문장 "OO는 XX를 말씀하신 건가요?" 와 같은 꼴
ASK_BACK = re.compile(r"말씀하신\s*건가요|무엇을\s*말씀|어떤\s*기술을\s*말씀|맞나요\?")


class Probe:
    """한 문항을 도는 동안 입력 검사 결과·발화 해석·모델 호출·추천 도구 결과를 옆에서 적는다(원문은 적지 않음)."""

    def __init__(self):
        self.guard = None
        self.turn = None
        self.model_calls = 0   # 실제 모델 호출(가짜 모드는 세지 않음)
        self.mock_turns = 0    # 가짜 대본 차례
        self.tokens = 0
        self.recommendations = []  # 상담 에이전트 recommend_courses 결과
        self.guardrail_calls = 0   # 실제 ApplyGuardrail 호출(모델 호출 수와 따로 셈)
        self.kb_retrieve_calls = 0  # 과정 검색의 KB Retrieve 호출(상한 없음, 개수만)
        self.embedding_calls = 0    # 임베딩 호출(상한 없음, 개수만. 캐시에 있으면 부르지 않음)
        self._embed_before = 0
        self._saved = None

    @staticmethod
    def _budget(name: str):
        value = os.getenv(name, "").strip()
        return int(value) if value else None

    def __enter__(self):
        check, interpret, call, call_tools, assess = (guardrail.check_input, intake.interpret,
                                                     bedrock._call, bedrock._call_tools, guardrail.bedrock_assess)
        self._saved = (check, interpret, call, call_tools, assess)
        from pipeline import embed, search

        self._embed, self._search = embed, search
        self._embed_before = embed.usage()["calls"]
        kb_scores = search._kb_scores
        self._saved_kb = kb_scores

        def counted_kb(*a, **k):
            self.kb_retrieve_calls += 1  # 실제 모드에서만 불린다(가짜 모드는 규칙 검색)
            return kb_scores(*a, **k)

        search._kb_scores = counted_kb
        model_budget, guard_budget = self._budget(MODEL_BUDGET_ENV), self._budget(GUARDRAIL_BUDGET_ENV)

        def watched_assess(text, source):
            if not bedrock.is_mock() and guardrail._guardrails_enabled(source):
                if guard_budget is not None and self.guardrail_calls >= guard_budget:
                    raise BudgetExceeded("ApplyGuardrail 호출 한도")
                self.guardrail_calls += 1
            return assess(text, source)

        def watched_check(text):
            self.guard = check(text)
            return self.guard

        def watched_interpret(*a, **k):
            self.turn = interpret(*a, **k)
            return self.turn

        def counted(fn):
            def wrapper(*a, **k):
                if not bedrock.is_mock() and model_budget is not None and self.model_calls >= model_budget:
                    raise BudgetExceeded("모델 호출 한도")  # 호출하기 전에 멈춘다
                result = fn(*a, **k)
                if bedrock.is_mock():
                    self.mock_turns += 1
                else:
                    self.model_calls += 1
                    usage = result[2] or {}
                    self.tokens += usage.get("totalTokens", usage.get("inputTokens", 0) + usage.get("outputTokens", 0))
                return result
            return wrapper

        guardrail.check_input = watched_check
        intake.interpret = watched_interpret
        bedrock._call, bedrock._call_tools = counted(call), counted(call_tools)
        guardrail.bedrock_assess = watched_assess
        return self

    def __exit__(self, *exc):
        (guardrail.check_input, intake.interpret, bedrock._call, bedrock._call_tools,
         guardrail.bedrock_assess) = self._saved
        self._search._kb_scores = self._saved_kb
        self.embedding_calls = self._embed.usage()["calls"] - self._embed_before
        return False


def guard_kind(result) -> str:
    if result is None:
        return "none"
    if guardrail.is_check_failure(result):
        return "check_failed"
    return "blocked" if result.blocked else "pass"


def screen_intent(probe: Probe) -> str:
    """구조 0 이 고른 의도. 막히면 blocked, 해석 결과의 status·action 으로 나머지를 정한다."""
    kind = guard_kind(probe.guard)
    if kind in ("blocked", "check_failed"):
        return kind
    turn = probe.turn
    if turn is None:
        return "no_turn"
    if turn.status != "ok":
        return turn.status  # out_of_scope, unsupported, clarify_role, incomplete, blocked
    return turn.action      # gap, courses, note, summary(구조 0 에는 demand 가 따로 없다)


def run_screen(case: dict) -> dict:
    """구조 0: 화면 경로 handle_message. 문항마다 새 대화."""
    from backend import chat as screen

    state = screen.Conversation()
    with Probe() as probe:
        start = time.perf_counter()
        reply = screen.handle_message(state, case["text"], job=case["job"], sidebar_skills=[])
        seconds = time.perf_counter() - start
    courses = reply.courses or []
    return {
        "guard": guard_kind(probe.guard),
        "intent": screen_intent(probe),
        "ask_back": bool(ASK_BACK.search(reply.text)),
        "courses": len(courses),
        "uncertain_courses": sum(1 for c in courses if c.get("match") == "uncertain"),
        "model_calls": probe.model_calls,
        "mock_turns": probe.mock_turns,
        "guardrail_calls": probe.guardrail_calls,
        "kb_retrieve_calls": probe.kb_retrieve_calls,
        "embedding_calls": probe.embedding_calls,
        "tokens": probe.tokens,
        "seconds": round(seconds, 3),
        "answer_chars": len(reply.text),
    }


def agent_intent(probe: Probe, result: dict) -> str:
    """구조 A 가 고른 의도. 막히면 blocked, 도구로 정한다. 도구 없이 범위 안 질문에 답하면 no_tool(에이전트가 표시)."""
    kind = guard_kind(probe.guard)
    if kind in ("blocked", "check_failed"):
        return kind
    names = [c["name"] for c in result["tool_calls"]]
    if "recommend_courses" in names:
        return "courses"
    if "get_gap" in names:
        return "gap"
    if "get_skill_demand" in names:
        return "demand"
    if result.get("uncertain_reason") == "no_tool":
        return "no_tool"
    return "out_of_scope"


def run_agent(case: dict) -> dict:
    """구조 A: 상담 에이전트 + recommend_courses. 문항마다 새 대화(기록 없음)."""
    from ai import agent

    with Probe() as probe:
        original = agent.tool_recommend_courses

        def watched(skills):
            out = original(skills)
            probe.recommendations.append(out)
            return out

        agent.tool_recommend_courses = watched
        try:
            start = time.perf_counter()
            result = agent.chat(case["text"])
            seconds = time.perf_counter() - start
        finally:
            agent.tool_recommend_courses = original
    courses = [c for out in probe.recommendations for r in out["recommendations"] for c in r["courses"]]
    return {
        "guard": guard_kind(probe.guard),
        "intent": agent_intent(probe, result),
        "ask_back": bool(ASK_BACK.search(result["answer"])),
        "courses": len(courses),
        "uncertain_courses": sum(1 for c in courses if c["match"] == "uncertain"),
        "model_calls": probe.model_calls,
        "mock_turns": probe.mock_turns,
        "guardrail_calls": probe.guardrail_calls,
        "kb_retrieve_calls": probe.kb_retrieve_calls,
        "embedding_calls": probe.embedding_calls,
        "tokens": probe.tokens,
        "seconds": round(seconds, 3),
        "answer_chars": len(result["answer"]),
        "tools": [c["name"] for c in result["tool_calls"]],
        "not_found": [s for out in probe.recommendations for s in out.get("not_found", [])],  # 수요 집계에 없어 추천하지 않은 기술
        "uncertain_reason": result.get("uncertain_reason"),
    }


STRUCTURES = {"0": run_screen, "A": run_agent}


def summarize(cases: list[dict], rows: dict[str, dict], structure: str = "") -> dict:
    """구조 하나의 지표. 비교 점수는 가드레일(regression)·참고(reference) 문항을 뺀 문항으로 센다.

    그 구조에 없는 의도를 기대하는 문항은 의도 정확도에서 "해당 없음"으로 빼고 따로 센다(SUPPORTED_INTENTS).
    """
    supported = SUPPORTED_INTENTS.get(structure)
    compared = [c for c in cases if not c.get("regression") and not c.get("reference")]
    not_applicable = [c for c in compared if supported is not None and c["expect_intent"] not in supported]
    scored = [c for c in compared if c not in not_applicable]
    ambiguous_all = [c for c in compared if c["category"] == "표기 애매"]
    ask_back_na = [] if structure in ASK_BACK_STRUCTURES or not structure else ambiguous_all
    ambiguous = [c for c in ambiguous_all if c not in ask_back_na]
    guarded = [c for c in cases if c.get("regression")]
    shown = sum(rows[c["id"]]["courses"] for c in cases)
    seconds = [rows[c["id"]]["seconds"] for c in cases]
    return {
        "intent_correct": sum(rows[c["id"]]["intent"] == c["expect_intent"] for c in scored), "intent_total": len(scored),
        "intent_wrong_ids": [c["id"] for c in scored if rows[c["id"]]["intent"] != c["expect_intent"]],
        "intent_not_applicable": len(not_applicable),
        "intent_not_applicable_ids": [c["id"] for c in not_applicable],
        "ask_back_correct": sum(rows[c["id"]]["ask_back"] == c["expect_ask_back"] for c in ambiguous),
        "ask_back_total": len(ambiguous),
        "ask_back_not_applicable": len(ask_back_na),
        "guard_blocked": sum(rows[c["id"]]["guard"] == "blocked" for c in guarded), "guard_total": len(guarded),
        "guard_check_failed": sum(rows[c["id"]]["guard"] == "check_failed" for c in cases),
        "uncertain_ratio": round(sum(rows[c["id"]]["uncertain_courses"] for c in cases) / shown, 3) if shown else None,
        "courses_shown": shown,
        "model_calls": sum(rows[c["id"]]["model_calls"] for c in cases),
        "guardrail_calls": sum(rows[c["id"]].get("guardrail_calls", 0) for c in cases),
        "kb_retrieve_calls": sum(rows[c["id"]].get("kb_retrieve_calls", 0) for c in cases),
        "embedding_calls": sum(rows[c["id"]].get("embedding_calls", 0) for c in cases),
        "tokens": sum(rows[c["id"]]["tokens"] for c in cases),
        "seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "seconds_max": max(seconds) if seconds else None,
    }


def run_isolated(structure: str, case_set: str, case_id: str, budgets: dict | None = None) -> dict:
    """(구조, 문항) 하나를 새 프로세스로 돌려 결과 행을 받는다. 메모리 캐시가 늘 비어 있다.

    하위 프로세스는 같은 환경 변수(BEDROCK_MOCK 등)를 물려받는다. 실패하면 멈춘다(통과로 세지 않음).
    오류 원문은 문항 내용이 섞일 수 있어 출력하지 않고 종료 코드만 알린다.
    """
    cmd = [sys.executable, "-m", "ai.eval.agent_compare", "--one", structure, case_id, "--set", case_set]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **{k: str(v) for k, v in (budgets or {}).items() if v is not None}}
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=CHILD_TIMEOUT)
    if proc.returncode == LIMIT_EXIT:
        raise LimitReached(f"구조 {structure} 문항 {case_id} 에서 호출 한도에 걸려 멈췄습니다")
    rows = [line[len(ROW_MARK):] for line in proc.stdout.splitlines() if line.startswith(ROW_MARK)]
    if proc.returncode != 0 or len(rows) != 1:
        raise RuntimeError(f"구조 {structure} 문항 {case_id} 하위 프로세스 실패(종료 코드 {proc.returncode}). 멈춥니다")
    return json.loads(rows[0])


def evaluate(cases: list[dict], structures: list[str], isolate: bool = False, case_set: str = "dev",
             max_model_calls: int | None = None, max_guardrail_calls: int | None = None,
             partial: dict | None = None) -> dict:
    """구조마다 문항을 돌려 지표를 낸다. isolate=True 면 (구조, 문항)마다 새 프로세스(실행 명령의 기본).

    max_* 는 이번 실행 전체의 호출 한도(구조·문항 합계)다. 남은 한도를 하위 프로세스에 넘기고, 넘기 직전에 멈춘다(LimitReached).
    partial 을 주면 멈출 때까지 끝난 행을 거기에 담는다(구조 → {문항 id → 행}).
    """
    out = {}
    used = {"model": 0, "guardrail": 0}
    partial = {} if partial is None else partial
    for name in structures:
        mine = [c for c in cases if not c.get("reference") or name == "0"]  # 참고 문항(AI 엔지니어)은 구조 0 만
        rows = partial.setdefault(name, {})
        for c in mine:
            if isolate:
                budgets = {MODEL_BUDGET_ENV: None if max_model_calls is None else max_model_calls - used["model"],
                           GUARDRAIL_BUDGET_ENV: None if max_guardrail_calls is None else max_guardrail_calls - used["guardrail"]}
                rows[c["id"]] = run_isolated(name, case_set, c["id"], budgets)
            else:
                rows[c["id"]] = STRUCTURES[name](c)
            used["model"] += rows[c["id"]]["model_calls"]
            used["guardrail"] += rows[c["id"]].get("guardrail_calls", 0)
        out[name] = {"summary": summarize(mine, rows, name), "rows": rows}
    return out


def run_one(structure: str, case_set: str, case_id: str) -> int:
    """하위 프로세스: 문항 하나를 돌려 결과 행을 ROW_MARK 한 줄로 출력한다(문항 원문은 출력하지 않음)."""
    path = HOLDOUT if case_set == "holdout" else CASES
    case = next((c for c in json.loads(path.read_text(encoding="utf-8"))["cases"] if c["id"] == case_id), None)
    if case is None or structure not in STRUCTURES:
        return 2
    try:
        row = STRUCTURES[structure](case)
    except BudgetExceeded as e:
        print(f"호출 한도에 걸려 멈춥니다: {e}")
        return LIMIT_EXIT
    print(ROW_MARK + json.dumps(row, ensure_ascii=False))
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="상담 구조 비교 코드 지표")
    ap.add_argument("--structures", nargs="+", default=["0"], help="비교할 구조(0, A)")
    ap.add_argument("--set", choices=("dev", "holdout"), default="dev")
    ap.add_argument("--bedrock", action="store_true", help="실제 모드(BEDROCK_MOCK=0 이어야 함)")
    ap.add_argument("--max-model-calls", type=int, help="이번 실행 전체의 모델 호출 한도(넘기 직전에 멈춤)")
    ap.add_argument("--max-guardrail-calls", type=int, help="이번 실행 전체의 ApplyGuardrail 호출 한도(넘기 직전에 멈춤)")
    ap.add_argument("--one", nargs=2, metavar=("STRUCTURE", "CASE_ID"), help=argparse.SUPPRESS)  # 하위 프로세스용
    args = ap.parse_args()
    if args.one:
        return run_one(args.one[0], args.set, args.one[1])
    unknown = [s for s in args.structures if s not in STRUCTURES]
    if unknown:
        print(f"아직 없는 구조라 건너뜁니다: {', '.join(unknown)}")
    structures = [s for s in args.structures if s in STRUCTURES]
    if args.bedrock == bedrock.is_mock():
        print("--bedrock 은 BEDROCK_MOCK=0 일 때만, 가짜 모드는 BEDROCK_MOCK=1 일 때만 씁니다. 멈춥니다.")
        return 1
    path = HOLDOUT if args.set == "holdout" else CASES
    if not path.exists():
        print(f"문항 파일이 없어 건너뜁니다: {path.relative_to(ROOT).as_posix()} (공개 범위 밖, 팀 저장소에만 있음)")
        return 0
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if not cases:
        print(f"문항이 없어 건너뜁니다: {path.relative_to(ROOT).as_posix()}")
        return 0
    if not structures:
        return 1
    if args.bedrock and (args.max_model_calls is None or args.max_guardrail_calls is None):
        print("실제 모드는 --max-model-calls 와 --max-guardrail-calls 를 함께 줘야 합니다. 멈춥니다.")
        return 1
    partial: dict = {}
    try:
        result = evaluate(cases, structures, isolate=True, case_set=args.set, max_model_calls=args.max_model_calls,
                          max_guardrail_calls=args.max_guardrail_calls, partial=partial)
    except LimitReached as e:
        done = {k: len(v) for k, v in partial.items()}
        used_m = sum(r["model_calls"] for v in partial.values() for r in v.values())
        used_g = sum(r.get("guardrail_calls", 0) for v in partial.values() for r in v.values())
        print(f"{e}. 끝난 문항 {done}, 쓴 호출: 모델 {used_m}번, ApplyGuardrail {used_g}번")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"agent_compare_{args.set}_stopped_{time.strftime('%Y%m%d-%H%M%S')}.json"
        kept = partial if args.set == "dev" else {k: len(v) for k, v in partial.items()}  # 보지 않은 문항은 개수만
        out.write_text(json.dumps({"cases": path.name, "notes": RESULT_NOTES, "stopped": str(e), "partial": kept},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"멈출 때까지의 결과: {out.relative_to(ROOT).as_posix()}")
        return LIMIT_EXIT
    for name, r in result.items():
        s = r["summary"]
        print(f"\n[구조 {name}] 의도 {s['intent_correct']}/{s['intent_total']}(해당 없음 {s['intent_not_applicable']})  되묻기 {s['ask_back_correct']}/{s['ask_back_total']}(해당 없음 {s['ask_back_not_applicable']})"
              f"  가드레일 차단 {s['guard_blocked']}/{s['guard_total']}(검사 실패 {s['guard_check_failed']})"
              f"  불확실 {s['uncertain_ratio']}  모델 {s['model_calls']}번  ApplyGuardrail {s['guardrail_calls']}번  KB 검색 {s['kb_retrieve_calls']}번  임베딩 {s['embedding_calls']}번  토큰 {s['tokens']}"
              f"  시간 중앙값 {s['seconds_median']}초 최대 {s['seconds_max']}초")
        if args.set == "dev":  # 보지 않은 문항은 숫자만
            print(f"  의도가 다른 문항: {', '.join(s['intent_wrong_ids']) or '없음'}"
                  f"  해당 없음: {', '.join(s['intent_not_applicable_ids']) or '없음'}")
            for cid, row in r["rows"].items():
                print(f"  {cid}: 의도 {row['intent']}, 입력 {row['guard']}, 되묻기 {row['ask_back']}, 과정 {row['courses']}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mode = "bedrock" if args.bedrock else "mock"
    out = OUT_DIR / f"agent_compare_{args.set}_{mode}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    saved = result if args.set == "dev" else {  # 보지 않은 문항은 숫자만 저장(문항 id 없음)
        k: {"summary": {m: n for m, n in v["summary"].items() if m not in ("intent_wrong_ids", "intent_not_applicable_ids")}} for k, v in result.items()}
    out.write_text(json.dumps({"cases": path.name, "notes": RESULT_NOTES, "result": saved}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n저장: {out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
