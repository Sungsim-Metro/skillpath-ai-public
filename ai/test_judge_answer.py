"""상담 답 채점(#94) 확인. 실행: python -m ai.test_judge_answer (가짜 모드, Bedrock 호출 없음)"""
import csv
import json
import os
import random
import tempfile
from pathlib import Path
from unittest import mock

os.environ["BEDROCK_MOCK"] = "1"

from ai import bedrock  # noqa: E402
from ai.eval import agent_compare as ac  # noqa: E402
from ai.eval import agreement as ag  # noqa: E402
from ai.eval import judge_answer as ja  # noqa: E402
from ai.prompts import judge_answer_v1 as prompt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {"id": "N1", "text": "질문 N1", "expect_intent": "gap"},
    {"id": "D1", "text": "질문 D1", "expect_intent": "demand"},
    {"id": "C2", "text": "질문 C2", "expect_intent": "courses"},
    {"id": "O1", "text": "질문 O1", "expect_intent": "out_of_scope"},
    {"id": "G1", "text": "질문 G1", "expect_intent": "blocked", "regression": True},
    {"id": "R1", "text": "질문 R1", "expect_intent": "gap", "reference": True},
]
ANSWERS = {s: {c["id"]: {"answer": f"{s} 의 답 {c['id']}", "evidence": "{}"} for c in CASES} for s in ("0", "A")}


def test_rubric_same_in_prompt_and_doc():
    # 지시문과 사람용 문서의 기준표가 글자까지 같다. 11/19 앵커 문장 그대로
    table = prompt.rubric_table()
    assert table in prompt.SYSTEM
    assert table in (ROOT / "ai" / "eval" / "JUDGE_ANSWER.md").read_text(encoding="utf-8")
    assert prompt.CRITERIA == ("helpful", "grounded")
    assert "질문에 바로 답하고, 다음에 무엇을 공부할지 구체적으로 알 수 있다" in table
    assert "도구 결과에 없는 숫자·과정을 지어내거나 단정한다" in table


def test_score_format():
    ok = {"scores": [{"item_id": "answer", "helpful": 5, "grounded": 1, "note": None}]}
    ag.validate_scores(ok, [prompt.ITEM_ID], prompt.CRITERIA)
    for bad in (0, 6, 3.0, "3", True, None):
        data = {"scores": [{"item_id": "answer", "helpful": bad, "grounded": 3, "note": None}]}
        try:
            ag.validate_scores(data, [prompt.ITEM_ID], prompt.CRITERIA)
        except ValueError:
            continue
        raise AssertionError(bad)
    for bad in ({"scores": [{"item_id": "다른 id", "helpful": 3, "grounded": 3, "note": None}]}, {"score": []}):
        try:
            ag.validate_scores(bad, [prompt.ITEM_ID], prompt.CRITERIA)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_only_answer_intents_are_judged():
    # 범위 밖·차단·참고 문항은 채점하지 않는다. 제외는 기대 의도로 정한다
    assert [c["id"] for c in CASES if ja.judged_case(c)] == ["N1", "D1", "C2"]
    items = ja.build_items(ANSWERS, CASES, random.Random(1))
    assert sorted((it["structure"], it["case_id"]) for it in items) == sorted(
        (s, cid) for s in ("0", "A") for cid in ("N1", "D1", "C2"))
    assert len({it["item_id"] for it in items}) == len(items)


def test_structure_name_hidden_from_judge():
    # 채점 입력에는 질문·답·근거만 들어간다(구조 이름·문항 id 없음)
    sent = []
    real = bedrock.converse_json

    def spy(system, user_text, **kw):
        sent.append((system, user_text))
        return real(system, user_text, **kw)

    items = ja.build_items(ANSWERS, CASES, random.Random(2))
    with mock.patch.object(bedrock, "converse_json", spy):
        ja.run_rounds(items, 1, None, [], {"calls": 0, "tokens": 0, "failed": 0})
    assert len(sent) == len(items)
    for _, user_text in sent:
        payload = json.loads(user_text)
        assert set(payload) == {"item_id", "question", "answer", "evidence"} and payload["item_id"] == prompt.ITEM_ID
        assert "structure" not in user_text and "case_id" not in user_text


def test_two_rounds_noise_and_effect():
    # 같은 답 두 번 = 잡음 쌍, 같은 질문의 0·A = 효과 쌍. 가짜 점수: default 4·4, C2 3·5, D1 5·3
    items = ja.build_items(ANSWERS, CASES, random.Random(3))
    done = []
    used = ja.run_rounds(items, 2, None, done, {"calls": 0, "tokens": 0, "failed": 0})
    assert len(done) == 12 and used["calls"] == 0 and used["failed"] == 0
    s = ja.summarize(done)
    assert s["by_structure"]["0"]["1"]["mean"] == {"helpful": 4.0, "grounded": 4.0}
    assert s["by_structure"]["A"]["2"]["distribution"]["helpful"] == {"1": 0, "2": 0, "3": 1, "4": 1, "5": 1}
    assert s["noise"]["identical"]["pairs"] == 6 and s["noise"]["noise"] == 0
    assert s["effect"]["changed"]["pairs"] == 3 and s["effect"]["labels"] == {"a": "0", "b": "A"}


def test_judge_model_must_differ():
    with mock.patch.dict(os.environ, {"JUDGE_MODEL_ID": "", "BEDROCK_MODEL_ID": "m-writer"}):
        _expect_error(ja.judge_model)
    with mock.patch.dict(os.environ, {"JUDGE_MODEL_ID": "m-writer", "BEDROCK_MODEL_ID": "m-writer"}):
        _expect_error(ja.judge_model)
    with mock.patch.dict(os.environ, {"JUDGE_MODEL_ID": "m-judge", "BEDROCK_MODEL_ID": "m-writer"}):
        assert ja.judge_model() == "m-judge"


def test_judge_call_limit_stops_before_call():
    # 실제 모드 흉내: 한 항목에 1번 호출. 한도 3이면 두 항목 뒤 남은 1번 < 최대 시도 2번이라 부르기 전에 멈춘다
    ok = bedrock.ConverseResult({"scores": [{"item_id": "answer", "helpful": 3, "grounded": 3, "note": None}]}, 1,
                                {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, ({"attempt": 1},))
    items = ja.build_items(ANSWERS, CASES, random.Random(4))
    done, used = [], {"calls": 0, "tokens": 0, "failed": 0}
    with mock.patch.object(bedrock, "is_mock", lambda: False), \
            mock.patch.dict(os.environ, {"JUDGE_MODEL_ID": "m-judge", "BEDROCK_MODEL_ID": "m-writer"}), \
            mock.patch.object(ja, "judge_one", lambda it: ok):
        _expect_error(lambda: ja.run_rounds(items, 1, 3, done, used), ja.JudgeLimitReached)
    assert len(done) == 2 and used == {"calls": 2, "tokens": 30, "failed": 0}


def test_human_sheet_hides_structure_and_agreement_reads_back():
    items = ja.build_items(ANSWERS, CASES, random.Random(5))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sheet.csv"
        ids = ja.write_human_sheet(path, items)
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert set(rows[0]) == {"item_id", "question", "answer", "evidence", "helpful", "grounded", "memo"}
        assert sorted(ids) == sorted(it["item_id"] for it in items)
        for r in rows:  # 사람이 4·4 를 적었다고 치고 일치율을 읽는다
            r.update(helpful="4", grounded="4")
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        done = []
        ja.run_rounds(items, 1, None, done, {"calls": 0, "tokens": 0, "failed": 0})
        agr = ja.human_agreement({"scored": done}, path)
    assert agr["by_criterion"]["helpful"]["n"] == len(items)
    assert agr["by_criterion"]["helpful"]["within_one"] == 1.0


def test_answers_split_from_compare_rows():
    # 답 원문·근거는 결과 행에서 떼어 답 파일로만 간다
    rows = {"0": {"N1": {"intent": "gap", "answer": "답", "evidence": "{}"}}}
    answers = ac.split_answers(rows)
    assert answers == {"0": {"N1": {"answer": "답", "evidence": "{}"}}} and rows == {"0": {"N1": {"intent": "gap"}}}
    assert len(ac.evidence_text({"x": "가" * (ac.EVIDENCE_MAX + 10)})) <= ac.EVIDENCE_MAX + len(" …(잘림)")


def test_no_judged_items_stops_with_notice():
    # 채점 대상(gap·demand·courses) 답이 하나도 없으면 안내만 하고 채점·결과 파일 없이 끝난다
    import argparse
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as d:
        answers = Path(d) / "agent_answers_dev_mock_x.json"
        answers.write_text(json.dumps({"set": "dev", "answers": {"0": {"O1": {"answer": "답", "evidence": "{}"}}}},
                                      ensure_ascii=False), encoding="utf-8")
        args = argparse.Namespace(judge=str(answers), bedrock=False, max_judge_calls=None, rounds=1, human_sheet=False)
        buf = io.StringIO()
        with mock.patch.object(ac, "OUT_DIR", Path(d) / "out"), contextlib.redirect_stdout(buf):
            code = ac.run_judge(args)
        assert code == 0 and "채점 대상이 없습니다" in buf.getvalue()
        assert not (Path(d) / "out").exists()


def _expect_error(fn, kind=ValueError):
    try:
        fn()
    except kind:
        return
    raise AssertionError(f"{kind.__name__} 가 나야 합니다")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("통과")
