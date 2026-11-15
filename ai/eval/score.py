"""추출 정답지 채점 (#8, #9).

실행 (저장소 루트에서):
  가짜 모드:  $env:BEDROCK_MOCK="1"; .venv\\Scripts\\python -m ai.eval.score --runs 1
  실제 모드:  $env:BEDROCK_MOCK="0"; .venv\\Scripts\\python -m ai.eval.score --runs 3
              (공고 10건 x 3회 = Bedrock 호출 30번 이상. 재시도가 있으면 더 늘어남)
  프롬프트:   --prompt v1(기본), v2, v2.1 (#21 채점용 공고로 판단할 버전은 v2.1)

채점은 전부 코드로 한다.
- 기본 점수: 공고마다 (normalized, type) 묶음을 정답과 비교해 모두 더한 micro P/R/F1
- 이름만 점수: type 을 빼고 normalized 만 비교
- extra: 정답에 없는데 뽑은 것. 정답이 빈 공고(0025)에서 하나라도 뽑으면 그만큼 extra
- 빈 공고 정답: 정답이 빈 공고에서 모델도 빈 목록을 내면 정답
- 지어낸 근거: evidence 가 공고 본문에 없는 결과 수 (uncertain 으로 남긴 것)
- section 일치: 이름이 맞은 기술 중 section 이 정답과 같은 수 (section 은 코드가 칸 제목으로 정한다)
- 제외 칸 버림: 혜택·회사소개 같은 칸에서 LLM 이 뽑아 코드가 버린 수 (기본 점수에는 넣지 않음)
결과 원본은 outputs/eval/ 에 저장한다 (.gitignore 대상).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
except ImportError:
    pass

from ai.bedrock import BedrockJSONError, is_mock  # noqa: E402
from ai.evidence import evidence_in_text  # noqa: E402
from ai.extract import DEFAULT_PROMPT, PROMPTS, extract_with_details  # noqa: E402

POSTINGS = ROOT / "data" / "sample" / "postings.json"
GOLD = ROOT / "ai" / "eval" / "gold_extraction.json"
OUT_DIR = ROOT / "outputs" / "eval"


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}


def run_once(postings: list[dict], gold: dict[str, dict], prompt_version: str = DEFAULT_PROMPT) -> dict:
    tp = fp = fn = 0
    name_tp = name_fp = name_fn = 0
    fabricated = uncertain = retries = 0
    empty_total = empty_ok = 0
    section_ok = section_total = excluded_dropped = 0
    tokens = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    rows, errors = [], []

    for p in postings:
        pid = p["id"]  # 정답지(gold)의 posting_id 와 같은 값
        gold_pairs = {(s["normalized"], s["type"]) for s in gold[pid]["skills"]}
        gold_names = {n for n, _ in gold_pairs}
        if not gold_pairs:
            empty_total += 1
        try:
            pred, meta, dropped = extract_with_details(p, prompt_version)
        except BedrockJSONError as e:
            errors.append({"posting_id": pid, "error": str(e)})
            fn += len(gold_pairs)
            name_fn += len(gold_names)
            continue

        pairs = {(s["normalized"], s["type"]) for s in pred["skills"]}
        names = {n for n, _ in pairs}
        tp += len(pairs & gold_pairs)
        fp += len(pairs - gold_pairs)
        fn += len(gold_pairs - pairs)
        name_tp += len(names & gold_names)
        name_fp += len(names - gold_names)
        name_fn += len(gold_names - names)
        if not gold_pairs and not pairs:
            empty_ok += 1

        gold_section = {s["normalized"]: s["section"] for s in gold[pid]["skills"]}
        section_wrong = []
        for s in pred["skills"]:
            if s["normalized"] in gold_section:
                section_total += 1
                if s["section"] == gold_section[s["normalized"]]:
                    section_ok += 1
                else:
                    section_wrong.append((s["normalized"], gold_section[s["normalized"]], s["section"]))
        excluded_dropped += len(dropped)

        made_up = [s for s in pred["skills"] if not evidence_in_text(s["evidence"], p["text"])]
        fabricated += len(made_up)
        uncertain += sum(s["confidence"] == "uncertain" for s in pred["skills"])
        retries += meta.attempts > 1
        for k in tokens:
            tokens[k] += meta.usage.get(k, 0)

        rows.append({
            "posting_id": pid,
            "attempts": meta.attempts,
            "extra": sorted(pairs - gold_pairs),
            "missing": sorted(gold_pairs - pairs),
            "fabricated_evidence": [(s["normalized"], s["evidence"]) for s in made_up],
            "section_wrong": section_wrong,  # (기술, 정답 section, 예측 section)
            "excluded_dropped": [(s["normalized"], s["evidence"]) for s in dropped],
            "prediction": pred,
        })

    return {
        "strict": prf(tp, fp, fn),
        "name_only": prf(name_tp, name_fp, name_fn),
        "counts": {
            "tp": tp, "extra": fp, "missing": fn,
            "fabricated_evidence": fabricated, "uncertain": uncertain,
            "empty_posting_ok": f"{empty_ok}/{empty_total}",
            "retried": retries, "errors": len(errors),
            "section_ok": f"{section_ok}/{section_total}", "excluded_dropped": excluded_dropped,
        },
        "tokens": tokens,
        "errors": errors,
        "rows": rows,
    }


def print_run(i: int, r: dict) -> None:
    s, n, c = r["strict"], r["name_only"], r["counts"]
    print(f"\n[{i}회차] P {s['precision']:.3f}  R {s['recall']:.3f}  F1 {s['f1']:.3f}"
          f"  (이름만 F1 {n['f1']:.3f})")
    print(f"  맞춤 {c['tp']} / extra {c['extra']} / 놓침 {c['missing']}"
          f" / 지어낸 근거 {c['fabricated_evidence']} / uncertain {c['uncertain']}")
    print(f"  빈 공고 정답 {c['empty_posting_ok']} / 재시도 {c['retried']}건 / 오류 {c['errors']}건"
          f" / 토큰 {r['tokens']['totalTokens']}")
    print(f"  section 일치 {c['section_ok']} / 제외 칸 버림 {c['excluded_dropped']}")
    for row in r["rows"]:
        if (row["extra"] or row["missing"] or row["fabricated_evidence"] or row["attempts"] > 1
                or row["section_wrong"] or row["excluded_dropped"]):
            print(f"  - {row['posting_id']}: 시도 {row['attempts']}회")
            for name, want, got in row["section_wrong"]:
                print(f"      section {name}: 정답 {want} / 예측 {got}")
            for name, ev in row["excluded_dropped"]:
                print(f"      제외 칸 버림 {name}: {ev!r}")
            if row["extra"]:
                print(f"      extra   {row['extra']}")
            if row["missing"]:
                print(f"      놓침    {row['missing']}")
            for name, ev in row["fabricated_evidence"]:
                print(f"      지어낸 근거 {name}: {ev!r}")
    for e in r["errors"]:
        print(f"  ! {e['posting_id']}: {e['error']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="추출 정답지 채점")
    ap.add_argument("--runs", type=int, default=3, help="반복 측정 횟수 (기본 3)")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default=DEFAULT_PROMPT, help="프롬프트 버전 (기본 v1)")
    args = ap.parse_args()

    prompt = PROMPTS[args.prompt]
    version = prompt.VERSION
    postings = json.loads(POSTINGS.read_text(encoding="utf-8"))
    gold = {g["posting_id"]: g for g in json.loads(GOLD.read_text(encoding="utf-8"))}
    n_gold = sum(len(g["skills"]) for g in gold.values())
    mode = "mock" if is_mock() else "bedrock"
    print(f"프롬프트 {version} / 모드 {mode} / 공고 {len(postings)}건 / 정답 {n_gold}개 / {args.runs}회")
    if mode == "mock":
        print("가짜 모드: data/sample/mock_bedrock_extract.json 의 응답입니다. 점수는 코드 경로 확인용이며 의미 없습니다.")

    runs = []
    for i in range(1, args.runs + 1):
        r = run_once(postings, gold, args.prompt)
        runs.append(r)
        print_run(i, r)

    if len(runs) > 1:
        f1s = [r["strict"]["f1"] for r in runs]
        print(f"\n{len(runs)}회 F1 범위: {min(f1s):.3f} ~ {max(f1s):.3f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{version}_{mode}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    meta = {"prompt": version, "mode": mode, "gold": n_gold, "alias_sha256": getattr(prompt, "ALIAS_SHA256", None)}
    out.write_text(json.dumps({**meta, "runs": runs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n원본 저장: {out.relative_to(ROOT)}")
    return 1 if any(r["errors"] for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
