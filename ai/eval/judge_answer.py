"""상담 답 채점(#94). agent_compare 가 저장한 답 파일(outputs/eval/agent_answers_*.json)을 채점 모델로 매긴다.

실행은 agent_compare 로 한다(`--judge`, `--agreement`). 기준표·채점 대상 규칙은 ai/eval/JUDGE_ANSWER.md.

- 채점 대상: 기대 의도가 gap·demand·courses 인 문항(범위 밖·차단·참고 문항 제외). 제외는 기대 의도로 정한다
- 구조 이름을 가린다: (구조, 문항)마다 무작위 item_id 를 붙이고 순서를 섞는다. 대응은 결과 파일에만 남긴다
- 답 하나씩 따로 채점한다(두 구조의 답을 나란히 보여 주지 않음). 채점 모델은 JUDGE_MODEL_ID, 답을 쓴 모델과 달라야 한다
- 같은 답을 rounds 번 채점하면 같은 글을 두 번 매긴 차이(채점 잡음)와 같은 질문의 0·A 차이(구조 효과)를 따로 센다
- 평균·분포·잡음·효과·일치율은 전부 코드로 계산한다(ai/eval/agreement.py)
- 답 원문·근거 자료는 outputs/ 파일에만 둔다. 화면에는 숫자만 출력한다
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from ai import bedrock
from ai.eval import agreement as ag
from ai.prompts import judge_answer_v1 as prompt

JUDGED_INTENTS = {"gap", "demand", "courses"}
MOCK_FILE = "mock_bedrock_judge_answer.json"
MAX_ATTEMPTS = 2  # 형식 오류면 한 번 재시도(채점 호출 한도는 시도마다 센다)


class JudgeLimitReached(RuntimeError):
    """채점 호출 한도에 닿아 멈춤(그때까지의 결과만 저장)."""


def judged_case(case: dict) -> bool:
    """채점 대상 문항인가. 기대 의도로만 정한다(구조가 실제로 낸 의도와 상관없음)."""
    return case.get("expect_intent") in JUDGED_INTENTS and not case.get("regression") and not case.get("reference")


def build_items(answers: dict, cases: list[dict], rng: random.Random) -> list[dict]:
    """채점할 항목. answers = {구조: {문항 id: {"answer", "evidence"}}}. 구조 이름은 item_id 로 가린다."""
    by_id = {c["id"]: c for c in cases}
    items = []
    for structure, rows in answers.items():
        for case_id, row in rows.items():
            case = by_id.get(case_id)
            if case is None or not judged_case(case):
                continue
            items.append({"item_id": f"{rng.getrandbits(32):08x}", "structure": structure, "case_id": case_id,
                          "question": case["text"], "answer": row["answer"], "evidence": row["evidence"]})
    rng.shuffle(items)
    return items


def judge_model() -> str:
    """실제 모드의 채점 모델. 비었거나 답을 쓴 모델(BEDROCK_MODEL_ID)과 같으면 멈춘다."""
    judge = os.getenv("JUDGE_MODEL_ID", "").strip()
    if not judge:
        raise ValueError("JUDGE_MODEL_ID 가 비어 있습니다. 채점 모델을 정해 주세요")
    if judge == os.getenv("BEDROCK_MODEL_ID", "").strip():
        raise ValueError("JUDGE_MODEL_ID 가 답을 쓴 모델(BEDROCK_MODEL_ID)과 같습니다. 다른 모델로 채점합니다")
    return judge


def judge_one(item: dict) -> bedrock.ConverseResult:
    """답 하나를 채점한다. 가짜 모드는 문항 id 를 키로 고정 점수를 쓴다(그 id 가 없으면 default)."""
    return bedrock.converse_json(
        prompt.SYSTEM, prompt.user_message(item["question"], item["answer"], item["evidence"]),
        mock_file=MOCK_FILE, mock_key=item["case_id"], usage_stage="judge_answer", max_tokens=400,
        max_attempts=MAX_ATTEMPTS,
        validate=lambda data: ag.validate_scores(data, [prompt.ITEM_ID], prompt.CRITERIA),
    )


def run_rounds(items: list[dict], rounds: int, max_calls: int | None, done: list[dict], used: dict) -> dict:
    """items 를 rounds 번 채점해 done 에 쌓는다. 한도는 시도(재시도 포함)마다 센다. 남은 한도가 한 항목의 최대 시도 수보다
    적으면 부르기 전에 멈춘다(JudgeLimitReached). 형식 검사에 두 번 실패한 항목은 scores 없이 남긴다(평균에서 빠짐)."""
    model = None if bedrock.is_mock() else judge_model()
    with bedrock.use_model(model):
        for r in range(1, rounds + 1):
            for it in items:
                if not bedrock.is_mock() and max_calls is not None and max_calls - used["calls"] < MAX_ATTEMPTS:
                    raise JudgeLimitReached(f"채점 호출 한도 {max_calls}번(쓴 것 {used['calls']}번)")
                row = {**{k: it[k] for k in ("item_id", "structure", "case_id", "answer")}, "round": str(r)}
                try:
                    result = judge_one(it)
                    calls = 0 if bedrock.is_mock() else len(result.calls)
                    tokens = result.usage.get("inputTokens", 0) + result.usage.get("outputTokens", 0)
                    s = result.data["scores"][0]
                    row.update(scores={k: s[k] for k in prompt.CRITERIA}, note=s.get("note"))
                except bedrock.BedrockJSONError as e:
                    calls = 0 if bedrock.is_mock() else MAX_ATTEMPTS
                    tokens = e.usage.get("inputTokens", 0) + e.usage.get("outputTokens", 0)
                    row.update(scores=None, note=None)
                    used["failed"] += 1
                used["calls"] += calls
                used["tokens"] += tokens
                done.append(row)
    return used


def summarize(scored: list[dict]) -> dict:
    """구조·회차별 평균과 분포, 같은 답을 두 번 채점한 잡음, 같은 질문의 0·A 차이(1회차). 전부 코드로 센다."""
    crit = prompt.CRITERIA
    out: dict = {"by_structure": {}}
    for structure in sorted({r["structure"] for r in scored}):
        mine = [r for r in scored if r["structure"] == structure]
        per_round = {}
        for rnd in sorted({r["round"] for r in mine}):
            rows = [r for r in mine if r["round"] == rnd and r["scores"]]
            per_round[rnd] = {
                "n": len(rows),
                "mean": {k: round(sum(r["scores"][k] for r in rows) / len(rows), 2) if rows else None for k in crit},
                "distribution": ag.distribution(rows, crit),
                "failed": sum(1 for r in mine if r["round"] == rnd and not r["scores"]),
            }
        out["by_structure"][structure] = per_round
    rounds = sorted({r["round"] for r in scored})
    out["noise"] = (ag.paired_change(scored, ("structure", "case_id"), "round", rounds[0], rounds[1], ("answer",), crit)
                    if len(rounds) >= 2 else None)
    first = [r for r in scored if r["round"] == rounds[0]] if rounds else []
    structures = sorted({r["structure"] for r in first})
    out["effect"] = (ag.paired_change(first, ("case_id",), "structure", structures[0], structures[1], ("answer",), crit)
                     if len(structures) == 2 else None)
    if out["effect"] is not None:
        out["effect"]["labels"] = {"a": structures[0], "b": structures[1]}  # higher = b 가 a 보다 합계가 높은 문항 수
    return out


def write_human_sheet(path: Path, items: list[dict]) -> list[str]:
    """사람 채점 양식. 구조 이름과 LLM 점수는 넣지 않는다(agreement.write_human_sheet)."""
    rows = sorted(items, key=lambda it: it["item_id"])
    return ag.write_human_sheet(path, rows, ["item_id", "question", "answer", "evidence"], prompt.CRITERIA)


def human_agreement(judge_result: dict, human_csv: Path) -> dict:
    """채점 결과 파일의 1회차 LLM 점수와 사람 채점 CSV 의 일치율."""
    first = min(r["round"] for r in judge_result["scored"])
    llm = {r["item_id"]: r["scores"] for r in judge_result["scored"] if r["round"] == first and r["scores"]}
    human = ag.read_human_sheet(human_csv, prompt.CRITERIA)
    return ag.agreement(llm, human, prompt.CRITERIA)


def judge_file(answers_path: Path, cases: list[dict], rounds: int, max_calls: int | None, out_dir: Path,
               human_sheet: bool = False, seed: int | None = None) -> tuple[Path, dict]:
    """답 파일을 채점하고 결과 파일 경로와 요약을 돌려준다. 한도에 닿으면 그때까지 결과를 저장하고 JudgeLimitReached."""
    data = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    seed = random.SystemRandom().randrange(2 ** 31) if seed is None else seed
    items = build_items(data["answers"], cases, random.Random(seed))
    done: list[dict] = []
    used = {"calls": 0, "tokens": 0, "failed": 0}
    stopped = None
    try:
        run_rounds(items, rounds, max_calls, done, used)
    except JudgeLimitReached as e:
        stopped = str(e)
    mode = "mock" if bedrock.is_mock() else "bedrock"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(done) if done else {}
    result = {
        "version": prompt.VERSION, "answers_file": Path(answers_path).name, "set": data.get("set"), "mode": mode,
        "judge_model": None if bedrock.is_mock() else os.getenv("JUDGE_MODEL_ID", "").strip(),
        "seed": seed, "rounds": rounds, "items": len(items), "judge_calls": used, "stopped": stopped,
        "summary": summary, "scored": done,
        "item_map": {it["item_id"]: {"structure": it["structure"], "case_id": it["case_id"]} for it in items},
    }
    out = out_dir / f"agent_judge_{data.get('set', 'dev')}_{mode}_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if human_sheet:
        write_human_sheet(out_dir / f"agent_judge_human_{data.get('set', 'dev')}_{stamp}.csv", items)
    if stopped:
        raise JudgeLimitReached(f"{stopped}. 그때까지의 결과: {out.name}")
    return out, result
