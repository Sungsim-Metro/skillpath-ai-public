"""일치율·분포·잡음·사람 양식(ai/eval/agreement.py) 확인. 실행: python -m ai.test_agreement

AWS 를 부르지 않는다. 숫자는 손으로 계산한 작은 예와 맞춘다.
"""
import tempfile
from pathlib import Path

from ai.eval import agreement as ag

CRIT = ("helpful", "grounded")


def _raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return True
    return False


def test_validate_scores():
    ok = {"scores": [{"item_id": "a", "helpful": 5, "grounded": 1, "note": None},
                     {"item_id": "b", "helpful": 3, "grounded": 4, "note": "짧음"}]}
    ag.validate_scores(ok, ["a", "b"], CRIT)
    assert _raises(ag.validate_scores, ok, ["b", "a"], CRIT)  # 순서가 다르면 오류
    for bad in (0, 6, "3", 3.0, None):
        data = {"scores": [{"item_id": "a", "helpful": bad, "grounded": 3, "note": None}]}
        assert _raises(ag.validate_scores, data, ["a"], CRIT), bad
    assert _raises(ag.validate_scores, {"scores": [{"item_id": "a", "helpful": 3, "grounded": 3, "note": 1}]}, ["a"], CRIT)
    assert _raises(ag.validate_scores, {}, ["a"], CRIT)


def test_weighted_kappa():
    assert ag.weighted_kappa([(1, 1), (3, 3), (5, 5)]) == 1.0          # 완전 일치
    assert ag.weighted_kappa([(1, 5), (5, 1), (3, 3)]) < 0             # 반대로 매김
    assert ag.weighted_kappa([(3, 1), (3, 5), (3, 2)]) == 0.0          # 한쪽만 모두 같은 점수 → 0(우연 수준)
    assert ag.weighted_kappa([(3, 3), (3, 3)]) is None                 # 둘 다 같은 한 점수 → 계산 불가
    assert ag.weighted_kappa([]) is None
    # 손 계산: 관찰 (1,1)(2,3), k=5, w=(i-j)^2/16. Σw·O = 1/16. 행 합 [1,1,0,0,0], 열 합 [1,0,1,0,0], n=2
    # Σw·E = (w11·1·1 + w13·1·1 + w21·1·1 + w23·1·1)/2 = (0 + 4/16 + 1/16 + 1/16)/2 = 3/16 → kappa = 1 − (1/16)/(3/16) = 0.667
    assert ag.weighted_kappa([(1, 1), (2, 3)]) == 0.667


def test_agreement_three_views():
    llm = {"a": {"helpful": 5, "grounded": 4}, "b": {"helpful": 3, "grounded": 2}, "c": {"helpful": 4, "grounded": 5},
           "d": {"helpful": 1, "grounded": 1}}
    human = {"a": {"helpful": 5, "grounded": 3}, "b": {"helpful": 2, "grounded": 2}, "c": {"helpful": 2, "grounded": None},
             "d": {"helpful": 1, "grounded": 1}}
    r = ag.agreement(llm, human, CRIT)
    assert (r["kappa_weight"], r["low_max"]) == ("quadratic", 3)
    h = r["by_criterion"]["helpful"]
    assert (h["n"], h["exact"], h["within_one"]) == (4, 0.5, 0.75)   # 같음 a·d, ±1 은 b 까지, c 는 2점 차
    assert h["mean_diff"] == 0.75 and h["confusion"]["2"]["4"] == 1   # 사람 2점·LLM 4점 = c
    assert (h["human_low"], h["human_low_caught"]) == (3, 2)          # 사람이 3점 이하: b·c·d, LLM 도 3점 이하: b·d
    assert h["kappa"] is not None
    g = r["by_criterion"]["grounded"]
    assert g["n"] == 3  # 사람이 빈 칸(c)은 세지 않는다


def test_distribution():
    items = [{"scores": {"helpful": 5, "grounded": 3}}, {"scores": {"helpful": 5, "grounded": 1}}, {"scores": None}]
    d = ag.distribution(items, CRIT)
    assert d["helpful"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 2} and d["grounded"]["1"] == 1


def test_paired_change_noise_vs_effect():
    def it(q, run, answer, h, g):
        return {"question": q, "run": run, "answer": answer, "scores": {"helpful": h, "grounded": g}}

    items = [it("q1", "1차", "같은 답", 4, 4), it("q1", "2차", "같은 답", 3, 4),   # 글이 같은데 점수가 다름: 잡음
             it("q2", "1차", "답 A", 3, 3), it("q2", "2차", "답 B", 5, 4),         # 글이 다름: 효과
             it("q3", "1차", "같다", 5, 5), it("q3", "2차", "같다", 5, 5)]
    r = ag.paired_change(items, ("question",), "run", "1차", "2차", ("answer",), CRIT)
    assert r["pairs"] == 3 and r["noise"] == 1
    assert r["identical"] == {"pairs": 2, "higher": 0, "same": 1, "lower": 1}
    assert r["changed"] == {"pairs": 1, "higher": 1, "same": 0, "lower": 0}
    assert ag.paired_change(items[:1], ("question",), "run", "1차", "2차", ("answer",), CRIT) is None


def test_human_sheet_round_trip_and_errors():
    with tempfile.TemporaryDirectory() as t:
        path = Path(t) / "sheet.csv"
        rows = [{"item_id": "x1", "question": "질문 하나", "answer": "답", "structure": "A"},
                {"item_id": "x2", "question": "질문 둘", "answer": "답2", "structure": "B"}]
        ids = ag.write_human_sheet(path, rows, ["item_id", "question", "answer"], CRIT)
        assert ids == ["x1", "x2"]
        text = path.read_text(encoding="utf-8-sig")
        assert text.splitlines()[0] == "item_id,question,answer,helpful,grounded,memo"
        assert "structure" not in text and ",A" not in text  # 어느 구조의 답인지 넣지 않는다
        # 사람이 채운 양식: 엑셀이 바꾼 "4.0", 빈 칸, 끝의 빈 줄, cp949 저장
        filled = "item_id,question,answer,helpful,grounded,memo\nx1,질문,답,4.0,,\nx2,질문,답,2,5,메모\n,,,,,\n"
        path.write_bytes(filled.encode("cp949"))
        got = ag.read_human_sheet(path, CRIT)
        assert got == {"x1": {"helpful": 4, "grounded": None}, "x2": {"helpful": 2, "grounded": 5}}
        for bad in ("x1,q,a,6,3,\n", "x1,q,a,3,3,\nx1,q,a,3,3,\n", ",q,a,3,3,\n", "x1,q,a,0,3,\n"):
            path.write_text("item_id,question,answer,helpful,grounded,memo\n" + bad, encoding="utf-8")
            assert _raises(ag.read_human_sheet, path, CRIT), bad


def test_no_gold_or_scoring_file_reference():
    # 채점용 정답지(data/raw/eval_test)를 읽는 코드는 가져오지 않았다
    src = Path(ag.__file__).read_text(encoding="utf-8")
    assert "gold_test" not in src and "eval_test" not in src and "GOLD_FILES" not in src and "count_cases" not in src


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("통과")
