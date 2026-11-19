"""가드레일(#24) 오탐률·미탐률. 계산은 전부 코드로 한다.

실행 (저장소 루트에서)
  python -m ai.eval.guardrail_eval              개선용 33문항(data/eval/guardrail_cases.json), 코드 검사
  python -m ai.eval.guardrail_eval --bedrock    Bedrock Guardrails 도 함께(실제 모드, GUARDRAIL_ID 필요). 문항마다 ApplyGuardrail 1번
  python -m ai.eval.guardrail_eval --holdout    보지 않은 문항(guardrail_cases_holdout.json). 규칙을 고정한 뒤 한 번만

- 오탐률 = 통과해야 할 문항 중 막힌 수 / 통과해야 할 문항 수
- 미탐률 = 막아야 할 문항 중 통과된 수 / 막아야 할 문항 수
- 방식별(코드만, Guardrails 모든 정책, Guardrails 필터만, 코드 또는 Guardrails, 코드 또는 Guardrails 필터)로 따로 센다.
  Guardrails 는 문항마다 한 번만 부른다. --bedrock 이 없으면 코드만
결과는 outputs/eval/guardrail_*.json 에 저장한다(.gitignore 대상). 개선용 문항은 팀이 지어낸 문장이라 화면에 출력한다.
--holdout 은 문항 id 와 범주만 출력한다(규칙 작성 쪽이 보지 않은 문항 원문을 보지 않게).
문항 파일은 차별 판단 요청 문장이 들어 있어 공개 범위(.github/public-sync/allowlist.txt) 밖인 data/eval/ 에 둔다.
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

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from ai import guardrail  # noqa: E402

EVAL_DATA = ROOT / "data" / "eval"  # 공개 범위 밖
CASES = EVAL_DATA / "guardrail_cases.json"
HOLDOUT = EVAL_DATA / "guardrail_cases_holdout.json"
TEST_SENTENCES = EVAL_DATA / "guardrail_test_sentences.json"
OUT_DIR = ROOT / "outputs" / "eval"


def rates(cases: list[dict], blocked: dict[str, bool]) -> dict:
    """오탐·미탐 수와 비율. blocked 는 문항 id → 막았는가."""
    should_pass = [c for c in cases if c["expect"] == "pass"]
    should_block = [c for c in cases if c["expect"] == "block"]
    false_pos = [c["id"] for c in should_pass if blocked[c["id"]]]
    false_neg = [c["id"] for c in should_block if not blocked[c["id"]]]
    return {
        "false_positive": len(false_pos), "should_pass": len(should_pass),
        "false_positive_rate": round(len(false_pos) / len(should_pass), 3) if should_pass else None,
        "false_negative": len(false_neg), "should_block": len(should_block),
        "false_negative_rate": round(len(false_neg) / len(should_block), 3) if should_block else None,
        "false_positive_ids": false_pos, "false_negative_ids": false_neg,
    }


def evaluate(cases: list[dict], use_bedrock: bool) -> dict:
    """방식별 오탐·미탐. Guardrails 는 문항마다 한 번만 불러 그 판정으로 여러 방식을 센다.

    code: 코드 검사 / guardrails: Guardrails 모든 정책 / guardrails_filters: Guardrails 필터만(차단 주제 제외)
    either: 코드 또는 Guardrails 모든 정책 / filters_plus_code: 코드 또는 Guardrails 필터만(GUARDRAIL_TOPIC_BLOCK=0, 시연 설정)
    """
    code = {c["id"]: guardrail.code_check(c["text"]).blocked for c in cases}
    out = {"code": rates(cases, code)}
    if use_bedrock:
        policies = {}
        for c in cases:
            try:
                policies[c["id"]] = guardrail.bedrock_assess(c["text"], "INPUT")
            except guardrail.GuardrailCallError:  # 설정 누락(ID·버전)·호출 실패·해석 불가
                policies[c["id"]] = None
        failed = [i for i, p in policies.items() if p is None]
        if failed:  # 호출 실패를 "통과"로 세면 표가 틀린다
            raise RuntimeError(f"Bedrock Guardrails 검사 실패 {len(failed)}/{len(cases)}건(ID·버전 설정, 자격 증명·권한 확인). 결과를 저장하지 않습니다")
        full = {i: guardrail.decide(p, topic_block=True).blocked for i, p in policies.items()}
        filters = {i: guardrail.decide(p, topic_block=False).blocked for i, p in policies.items()}
        out["guardrails"] = rates(cases, full)
        out["guardrails_filters"] = rates(cases, filters)
        out["either"] = rates(cases, {i: code[i] or full[i] for i in code})
        out["filters_plus_code"] = rates(cases, {i: code[i] or filters[i] for i in code})
        out["policies"] = {i: [f"{c}:{n}" for c, n in (p or [])] for i, p in policies.items()}  # 막은 정책(원문 없음)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="가드레일 오탐률·미탐률")
    ap.add_argument("--bedrock", action="store_true", help="Bedrock Guardrails 도 함께(실제 모드, GUARDRAIL_ID 필요)")
    ap.add_argument("--holdout", action="store_true", help="보지 않은 문항으로(규칙을 고정한 뒤 한 번만)")
    args = ap.parse_args()
    path = HOLDOUT if args.holdout else CASES
    if not path.exists():  # 공개본에는 data/eval/ 을 내보내지 않는다(차별 판단 요청 문장)
        print(f"평가 문항 파일이 없어 건너뜁니다: {path.relative_to(ROOT).as_posix()} (공개 범위 밖, 팀 저장소에만 있음)")
        return 0
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if not cases:
        print(f"문항이 없습니다: {path.relative_to(ROOT)}")
        return 1
    if args.bedrock and not guardrail._guardrails_enabled("INPUT"):
        print("가짜 모드라 Bedrock Guardrails 를 부르지 않습니다(BEDROCK_MOCK=0 필요). 코드 검사만 잽니다.")
        args.bedrock = False
    result = evaluate(cases, args.bedrock)
    print(f"문항 {len(cases)}개 ({path.name}) / 막을 {sum(c['expect'] == 'block' for c in cases)} / 통과할 {sum(c['expect'] == 'pass' for c in cases)}")
    by_id = {c["id"]: c for c in cases}
    for method, r in result.items():
        if method == "policies":
            continue
        print(f"\n[{method}] 오탐 {r['false_positive']}/{r['should_pass']} ({r['false_positive_rate']:.1%})"
              f"  미탐 {r['false_negative']}/{r['should_block']} ({r['false_negative_rate']:.1%})")
        for i in r["false_positive_ids"]:
            print(f"  오탐 {i}" + ("" if args.holdout else f": {by_id[i]['text']}"))
        for i in r["false_negative_ids"]:
            print(f"  미탐 {i} ({by_id[i]['category']})" + ("" if args.holdout else f": {by_id[i]['text']}"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"guardrail_{'holdout' if args.holdout else 'dev'}_{'bedrock' if args.bedrock else 'code'}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"cases": path.name, "result": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
