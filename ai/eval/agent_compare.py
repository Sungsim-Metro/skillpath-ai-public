"""상담 구조 비교(#93·#94). 같은 질문 세트를 구조마다 한 번씩 돌려 코드 지표를 센다. 계산은 전부 코드로 한다.

실행 (저장소 루트에서)
  python -m ai.eval.agent_compare                       개선용 문항(data/eval/agent_cases.json), 구조 0, 가짜 모드
  python -m ai.eval.agent_compare --structures 0 A      구조 0(화면 경로)과 A(상담 에이전트, 아직 없음)
  python -m ai.eval.agent_compare --bedrock             실제 모드(BEDROCK_MOCK=0 과 모델·가드레일·DynamoDB 설정 필요)
  python -m ai.eval.agent_compare --set holdout         보지 않은 문항. 숫자만 출력(문항 id·원문 없음). 채점 직전에 한 번만

구조
- 0 화면 경로: backend.chat.handle_message(가드레일 → 발화 해석 → 코드 분기 → Gap 또는 검토 추천). 지금 화면이 쓰는 경로
- A 상담 에이전트 + 추천 도구: feat/agent-recommend-tool 에서 붙인다(아직 없음)
- 세 구조 모두 입력 검사는 ai.guardrail.check_input 하나다. 가드레일 문항(regression)은 비교 점수에서 빼고 결과가 같은지만 본다

지표 (문항마다 기록하고 코드로 합친다)
- 의도 정확도: 관찰한 의도(gap·demand·courses·out_of_scope·blocked 등)가 expect_intent 와 같은 비율(가드레일·참고 문항 제외).
  그 구조에 없는 의도를 기대하는 문항(구조 0 의 demand)은 "해당 없음"으로 따로 세고 분모에서 뺀다
- 되묻기 정확도: 표기 애매 문항에서 되묻기 여부가 expect_ask_back 과 같은 비율(답 문장의 되묻기 표현으로 판정)
- 가드레일: 정책상 차단·통과·검사 실패를 따로 센다. 검사 실패를 차단으로 세지 않는다
- 불확실 비율: 보여 준 과정 중 match 가 uncertain 인 비율
- 모델 호출 수, 토큰, 시간(중앙값·최대)
결과는 outputs/eval/agent_compare_*.json 에 저장한다(.gitignore 대상).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
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
# 답에 되묻기가 있는지(표기 애매 문항). 상담 에이전트의 보충 문장 "OO는 XX를 말씀하신 건가요?" 와 같은 꼴
ASK_BACK = re.compile(r"말씀하신\s*건가요|무엇을\s*말씀|어떤\s*기술을\s*말씀|맞나요\?")


class Probe:
    """한 문항을 도는 동안 입력 검사 결과·발화 해석·모델 호출을 옆에서 적는다(원문은 적지 않음)."""

    def __init__(self):
        self.guard = None
        self.turn = None
        self.model_calls = 0
        self._saved = None

    def __enter__(self):
        check, interpret, call, call_tools = (guardrail.check_input, intake.interpret,
                                             bedrock._call, bedrock._call_tools)
        self._saved = (check, interpret, call, call_tools)

        def watched_check(text):
            self.guard = check(text)
            return self.guard

        def watched_interpret(*a, **k):
            self.turn = interpret(*a, **k)
            return self.turn

        def counted(fn):
            def wrapper(*a, **k):
                self.model_calls += 1
                return fn(*a, **k)
            return wrapper

        guardrail.check_input = watched_check
        intake.interpret = watched_interpret
        bedrock._call, bedrock._call_tools = counted(call), counted(call_tools)
        return self

    def __exit__(self, *exc):
        guardrail.check_input, intake.interpret, bedrock._call, bedrock._call_tools = self._saved
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
        "tokens": state.usage.total_tokens,
        "seconds": round(seconds, 3),
        "answer_chars": len(reply.text),
    }


STRUCTURES = {"0": run_screen}  # "A" 는 feat/agent-recommend-tool 에서 더한다


def summarize(cases: list[dict], rows: dict[str, dict], structure: str = "") -> dict:
    """구조 하나의 지표. 비교 점수는 가드레일(regression)·참고(reference) 문항을 뺀 문항으로 센다.

    그 구조에 없는 의도를 기대하는 문항은 의도 정확도에서 "해당 없음"으로 빼고 따로 센다(SUPPORTED_INTENTS).
    """
    supported = SUPPORTED_INTENTS.get(structure)
    compared = [c for c in cases if not c.get("regression") and not c.get("reference")]
    not_applicable = [c for c in compared if supported is not None and c["expect_intent"] not in supported]
    scored = [c for c in compared if c not in not_applicable]
    ambiguous = [c for c in scored if c["category"] == "표기 애매"]
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
        "guard_blocked": sum(rows[c["id"]]["guard"] == "blocked" for c in guarded), "guard_total": len(guarded),
        "guard_check_failed": sum(rows[c["id"]]["guard"] == "check_failed" for c in cases),
        "uncertain_ratio": round(sum(rows[c["id"]]["uncertain_courses"] for c in cases) / shown, 3) if shown else None,
        "courses_shown": shown,
        "model_calls": sum(rows[c["id"]]["model_calls"] for c in cases),
        "tokens": sum(rows[c["id"]]["tokens"] for c in cases),
        "seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "seconds_max": max(seconds) if seconds else None,
    }


def evaluate(cases: list[dict], structures: list[str]) -> dict:
    out = {}
    for name in structures:
        mine = [c for c in cases if not c.get("reference") or name == "0"]  # 참고 문항(AI 엔지니어)은 구조 0 만
        rows = {c["id"]: STRUCTURES[name](c) for c in mine}
        out[name] = {"summary": summarize(mine, rows, name), "rows": rows}
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="상담 구조 비교 코드 지표")
    ap.add_argument("--structures", nargs="+", default=["0"], help="비교할 구조(0, A)")
    ap.add_argument("--set", choices=("dev", "holdout"), default="dev")
    ap.add_argument("--bedrock", action="store_true", help="실제 모드(BEDROCK_MOCK=0 이어야 함)")
    args = ap.parse_args()
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
    result = evaluate(cases, structures)
    for name, r in result.items():
        s = r["summary"]
        print(f"\n[구조 {name}] 의도 {s['intent_correct']}/{s['intent_total']}(해당 없음 {s['intent_not_applicable']})  되묻기 {s['ask_back_correct']}/{s['ask_back_total']}"
              f"  가드레일 차단 {s['guard_blocked']}/{s['guard_total']}(검사 실패 {s['guard_check_failed']})"
              f"  불확실 {s['uncertain_ratio']}  모델 {s['model_calls']}번  토큰 {s['tokens']}"
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
    out.write_text(json.dumps({"cases": path.name, "result": saved}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
