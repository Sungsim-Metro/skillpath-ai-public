"""상담 구조 비교 스크립트(ai/eval/agent_compare.py) 확인. 실행: python -m ai.test_agent_compare

가짜 모드라 AWS 를 부르지 않는다. 문항 파일(data/eval/)이 없으면(공개본) 문항이 필요한 테스트는 건너뛴다.
"""
import json
import os

os.environ["BEDROCK_MOCK"] = "1"
os.environ["USAGE_LOG"] = "off"

from ai import bedrock, guardrail  # noqa: E402
from ai import chat as intake  # noqa: E402
from ai.eval import agent_compare as ac  # noqa: E402

for _name in ("GUARDRAIL_ID", "GUARDRAIL_VERSION", "BEDROCK_GUARDRAIL_ID", "BEDROCK_GUARDRAIL_VERSION"):
    os.environ.pop(_name, None)  # .env 값에 영향받지 않게


def _row(intent, guard="pass", ask_back=False, courses=0, uncertain=0, calls=0, tokens=0, seconds=1.0):
    return {"intent": intent, "guard": guard, "ask_back": ask_back, "courses": courses, "uncertain_courses": uncertain,
            "model_calls": calls, "tokens": tokens, "seconds": seconds, "answer_chars": 10}


def test_summary_counts_by_code():
    cases = [
        {"id": "a", "category": "정상 상담", "expect_intent": "gap", "expect_ask_back": False},
        {"id": "b", "category": "표기 애매", "expect_intent": "gap", "expect_ask_back": True},
        {"id": "c", "category": "과정 추천", "expect_intent": "courses", "expect_ask_back": False},
        {"id": "g", "category": "가드레일", "expect_intent": "blocked", "expect_ask_back": False, "regression": True},
        {"id": "h", "category": "가드레일", "expect_intent": "blocked", "expect_ask_back": False, "regression": True},
        {"id": "r", "category": "참고", "expect_intent": "gap", "expect_ask_back": False, "reference": True},
    ]
    rows = {"a": _row("gap", calls=1, tokens=10, seconds=1), "b": _row("note", ask_back=True, calls=1, tokens=10, seconds=3),
            "c": _row("courses", courses=4, uncertain=1, calls=3, tokens=30, seconds=5),
            "g": _row("blocked", guard="blocked"), "h": _row("check_failed", guard="check_failed"),
            "r": _row("courses", seconds=2)}
    s = ac.summarize(cases, rows)
    # 의도는 가드레일·참고를 뺀 3문항, 되묻기는 표기 애매 1문항
    assert (s["intent_correct"], s["intent_total"], s["intent_wrong_ids"]) == (2, 3, ["b"])
    assert (s["ask_back_correct"], s["ask_back_total"]) == (1, 1)
    # 검사 실패는 차단으로 세지 않는다
    assert (s["guard_blocked"], s["guard_total"], s["guard_check_failed"]) == (1, 2, 1)
    assert s["uncertain_ratio"] == 0.25 and s["courses_shown"] == 4
    assert (s["model_calls"], s["tokens"], s["seconds_max"]) == (5, 50, 5)


def test_intent_not_applicable_for_structure_0():
    # 구조 0 에 없는 의도(demand)를 기대하는 문항은 "해당 없음"으로 따로 세고 분모에서 뺀다. 다른 구조는 그대로 센다
    cases = [
        {"id": "a", "category": "정상 상담", "expect_intent": "gap", "expect_ask_back": False},
        {"id": "d1", "category": "기술 하나 수요", "expect_intent": "demand", "expect_ask_back": False},
        {"id": "d2", "category": "기술 하나 수요", "expect_intent": "demand", "expect_ask_back": False},
    ]
    rows = {"a": _row("gap"), "d1": _row("note"), "d2": _row("demand")}
    s0 = ac.summarize(cases, rows, "0")
    assert (s0["intent_correct"], s0["intent_total"], s0["intent_not_applicable"]) == (1, 1, 2)
    assert s0["intent_not_applicable_ids"] == ["d1", "d2"] and s0["intent_wrong_ids"] == []
    sa = ac.summarize(cases, rows, "A")
    assert (sa["intent_correct"], sa["intent_total"], sa["intent_not_applicable"]) == (2, 3, 0)
    assert sa["intent_wrong_ids"] == ["d1"]


def test_screen_intent_mapping():
    p = ac.Probe()
    p.guard = guardrail.GuardResult(True, "guardrails", guardrail.FAILED, "RuntimeError")
    assert ac.screen_intent(p) == "check_failed"
    p.guard = guardrail.GuardResult(True, "code", "공격", "지시 무시")
    assert ac.screen_intent(p) == "blocked"
    p.guard = guardrail.PASS
    p.turn = intake.Turn(status="out_of_scope")
    assert ac.screen_intent(p) == "out_of_scope"
    p.turn = intake.Turn(status="ok", action="courses")
    assert ac.screen_intent(p) == "courses"


def test_ask_back_not_applicable_for_structure_0():
    # 구조 0 에는 기술 표기 되묻기 기능이 없어 표기 애매 문항의 되묻기를 "해당 없음"으로 뺀다. A·B 는 센다
    cases = [{"id": "t1", "category": "표기 애매", "expect_intent": "gap", "expect_ask_back": True},
             {"id": "t2", "category": "표기 애매", "expect_intent": "gap", "expect_ask_back": True}]
    rows = {"t1": _row("gap", ask_back=False), "t2": _row("gap", ask_back=True)}
    s0 = ac.summarize(cases, rows, "0")
    assert (s0["ask_back_correct"], s0["ask_back_total"], s0["ask_back_not_applicable"]) == (0, 0, 2)
    assert s0["intent_total"] == 2  # 의도는 그대로 센다
    sa = ac.summarize(cases, rows, "A")
    assert (sa["ask_back_correct"], sa["ask_back_total"], sa["ask_back_not_applicable"]) == (1, 2, 0)


def test_agent_intent_mapping():
    p = ac.Probe()
    p.guard = guardrail.PASS

    def res(names, reason=None):
        return {"tool_calls": [{"name": n} for n in names], "uncertain_reason": reason}

    assert ac.agent_intent(p, res(["get_gap", "recommend_courses"])) == "courses"
    assert ac.agent_intent(p, res(["get_gap"])) == "gap"
    assert ac.agent_intent(p, res(["get_skill_demand"])) == "demand"
    assert ac.agent_intent(p, res([], "no_tool")) == "no_tool"      # 범위 안 질문인데 도구를 안 부름
    assert ac.agent_intent(p, res([])) == "out_of_scope"
    p.guard = guardrail.GuardResult(True, "guardrails", guardrail.FAILED, "RuntimeError")
    assert ac.agent_intent(p, res([])) == "check_failed"


def test_probe_counts_real_calls_only():
    # 가짜 모드의 대본 차례는 mock_turns 로, 실제 모델 호출과 토큰은 응답의 usage 로 센다
    old_mock = os.environ.get("BEDROCK_MOCK")
    saved = bedrock._call
    try:
        bedrock._call = lambda *a, **k: ("{}", "end_turn", {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10})
        os.environ["BEDROCK_MOCK"] = "0"
        with ac.Probe() as p:
            bedrock._call("s", [], 10, 0.0, "", "k", 1)
            bedrock._call("s", [], 10, 0.0, "", "k", 2)
        assert (p.model_calls, p.tokens, p.mock_turns) == (2, 20, 0)
        os.environ["BEDROCK_MOCK"] = "1"
        with ac.Probe() as p:
            bedrock._call("s", [], 10, 0.0, "", "k", 1)
        assert (p.model_calls, p.tokens, p.mock_turns) == (0, 0, 1)
    finally:
        bedrock._call = saved
        os.environ["BEDROCK_MOCK"] = old_mock or "1"


def test_probe_restores_patches():
    saved = (guardrail.check_input, intake.interpret, bedrock._call, bedrock._call_tools)
    with ac.Probe():
        assert guardrail.check_input is not saved[0]
    assert (guardrail.check_input, intake.interpret, bedrock._call, bedrock._call_tools) == saved


def test_ask_back_pattern():
    assert ac.ASK_BACK.search("도카는 Docker를 말씀하신 건가요?")
    assert not ac.ASK_BACK.search("부족한 기술은 Kubernetes 입니다.")


def test_mock_run_on_dev_cases():
    if not ac.CASES.exists():
        print("  건너뜀: test_mock_run_on_dev_cases (data/eval/ 없음)")
        return
    cases = json.loads(ac.CASES.read_text(encoding="utf-8"))["cases"]
    assert sum(1 for c in cases if not c.get("reference")) == 12  # 개선용 12문항(+ 참고 문항)
    assert all(c["job"] == "cloud_engineer" for c in cases if not c.get("reference"))
    both = ac.evaluate(cases, ["0", "A"])
    a = both["A"]
    assert set(a["rows"]) == {c["id"] for c in cases if not c.get("reference")}  # 참고 문항(AI 엔지니어)은 구조 0 만
    assert a["summary"]["model_calls"] == 0 and a["summary"]["intent_not_applicable"] == 0  # A 는 해당 없음이 없다
    assert a["rows"]["G1"]["guard"] == a["rows"]["G2"]["guard"] == "blocked"  # 같은 입력 검사
    assert all("not_found" in row for row in a["rows"].values())  # 수요 집계에 없어 추천하지 않은 기술을 남긴다
    r = both["0"]
    assert set(r["rows"]) == {c["id"] for c in cases}
    s = r["summary"]
    assert s["model_calls"] == 0 and s["guard_check_failed"] == 0  # 가짜 모드: 모델·Guardrails 를 부르지 않음
    assert s["guard_blocked"] == s["guard_total"] == 2  # 가드레일 회귀 문항은 코드 검사로 막힌다
    assert (s["intent_total"], s["intent_not_applicable"]) == (8, 2)  # 구조 0: D1·D2 는 해당 없음
    assert (s["ask_back_total"], s["ask_back_not_applicable"]) == (0, 2)  # 구조 0: T1·T2 되묻기는 해당 없음
    assert (a["summary"]["ask_back_total"], a["summary"]["ask_back_not_applicable"]) == (2, 0)


def test_budget_stops_before_call():
    # 한도를 넘기 직전에 멈춘다(호출하지 않음). BaseException 이라 except Exception 이 삼키지 않는다
    old_mock, saved = os.environ.get("BEDROCK_MOCK"), bedrock._call
    calls = []
    try:
        bedrock._call = lambda *a, **k: calls.append(1) or ("{}", "end_turn", {"totalTokens": 1})
        os.environ.update({"BEDROCK_MOCK": "0", ac.MODEL_BUDGET_ENV: "2"})
        with ac.Probe():
            bedrock._call("s", [], 10, 0.0, "", "k", 1)
            bedrock._call("s", [], 10, 0.0, "", "k", 2)
            try:
                try:
                    bedrock._call("s", [], 10, 0.0, "", "k", 3)
                except Exception:  # 상담 코드처럼 except Exception 으로 감싸도
                    raise AssertionError("한도 초과가 except Exception 에 잡힘")
            except ac.BudgetExceeded:
                pass
            else:
                raise AssertionError("한도를 넘었는데 멈추지 않음")
        assert len(calls) == 2  # 세 번째는 호출하지 않았다
    finally:
        bedrock._call = saved
        os.environ.pop(ac.MODEL_BUDGET_ENV, None)
        os.environ["BEDROCK_MOCK"] = old_mock or "1"
    assert issubclass(ac.BudgetExceeded, BaseException) and not issubclass(ac.BudgetExceeded, Exception)


def test_kb_and_embedding_calls_are_counted():
    # 과정 검색의 KB Retrieve 와 임베딩 호출은 상한 없이 개수만 센다(가짜 함수로, 네트워크 없음)
    from pipeline import embed, search

    saved_kb = search._kb_scores
    try:
        search._kb_scores = lambda *a, **k: {}
        with ac.Probe() as p:
            search._kb_scores("Docker")
            search._kb_scores("Docker", ["X"])
            embed._usage["calls"] += 3  # 임베딩 3번 부른 것과 같게
        assert (p.kb_retrieve_calls, p.embedding_calls) == (2, 3)
        assert search._kb_scores("x") == {}  # 원래대로 돌아왔다(가짜 함수)
    finally:
        search._kb_scores = saved_kb
        embed._usage["calls"] -= 3
    assert any("KB Retrieve" in n for n in ac.RESULT_NOTES)


def test_limit_exit_stops_parent():
    # 하위 프로세스가 한도 종료 코드로 끝나면 부모는 LimitReached 로 멈춘다
    old = ac.subprocess.run
    try:
        ac.subprocess.run = lambda *a, **k: type("P", (), {"returncode": ac.LIMIT_EXIT, "stdout": ""})()
        try:
            ac.run_isolated("A", "dev", "C1", {ac.MODEL_BUDGET_ENV: 0})
        except ac.LimitReached as e:
            assert "C1" in str(e)
        else:
            raise AssertionError("한도 종료인데 멈추지 않음")
    finally:
        ac.subprocess.run = old


def test_isolated_run_matches_in_process():
    # (구조, 문항)마다 새 프로세스로 돌린 결과 행이 같은 프로세스에서 돌린 것과 같다(시간 제외). 캐시가 늘 비어 있다
    if not ac.CASES.exists():
        print("  건너뜀: test_isolated_run_matches_in_process (data/eval/ 없음)")
        return
    cases = json.loads(ac.CASES.read_text(encoding="utf-8"))["cases"]
    case = next(c for c in cases if c["id"] == "C1")
    for name in ("0", "A"):
        here = ac.STRUCTURES[name](case)
        there = ac.run_isolated(name, "dev", "C1")
        assert {k: v for k, v in here.items() if k != "seconds"} == {k: v for k, v in there.items() if k != "seconds"}, name
    try:
        ac.run_isolated("0", "dev", "없는문항")
    except RuntimeError as e:
        assert "없는문항" in str(e) and "멈춥니다" in str(e)
    else:
        raise AssertionError("없는 문항인데 멈추지 않음")
    assert len(ac.RESULT_NOTES) == 4 and "새 프로세스" in ac.RESULT_NOTES[1] and "_analyze" in ac.RESULT_NOTES[3]


def test_holdout_placeholder_is_empty_schema():
    # 보지 않은 문항 자리: 비어 있으면 스크립트가 건너뛴다(내용은 채점 직전에 넣음)
    if not ac.HOLDOUT.exists():
        print("  건너뜀: test_holdout_placeholder_is_empty_schema (data/eval/ 없음)")
        return
    data = json.loads(ac.HOLDOUT.read_text(encoding="utf-8"))
    assert "cases" in data and isinstance(data["cases"], list)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("통과")
