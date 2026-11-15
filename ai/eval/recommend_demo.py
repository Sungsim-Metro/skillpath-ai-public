"""추천 이유 v1 데모 (#15). 임시 연결표의 기술로 ③ 을 만들고 검사 결과와 토큰을 보여준다.

실행 (저장소 루트에서):
  가짜 모드:  $env:BEDROCK_MOCK="1"; .venv\\Scripts\\python -m ai.eval.recommend_demo
  실제 모드:  $env:BEDROCK_MOCK="0"; $env:BEDROCK_MODEL_ID="..."; $env:AWS_PROFILE="kosa"
              .venv\\Scripts\\python -m ai.eval.recommend_demo     (기술 1개당 호출 1번, 재시도 시 1번 더)

수요 숫자는 두 모드 모두 가짜 data/sample/demand.json 에서 코드로 계산한다 (실제 집계 #12 전).
reason 의 내용이 학습 목표와 맞는지는 코드로 다 볼 수 없어서, 사람이 볼 수 있게 학습 목표를 함께 출력한다.
결과 원본은 outputs/eval/ 에 저장한다 (.gitignore 대상).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
except ImportError:
    pass

from ai.bedrock import BedrockJSONError, add_usage, is_mock  # noqa: E402
from ai.recommend import PROMPT_VERSION, build_recommendation, candidate_courses  # noqa: E402

DEMAND = ROOT / "data" / "sample" / "demand.json"
OUT_DIR = ROOT / "outputs" / "eval"
DEFAULT_SKILLS = ["Kubernetes", "Docker"]


def demand_of(skill: str) -> dict:
    """③ demand. backend.get_recommendations 의 가짜 모드와 같은 계산 (건수 기준, 단위 미정)."""
    d = json.loads(DEMAND.read_text(encoding="utf-8"))
    s = next(x for x in d["skills"] if x["skill"] == skill)
    return {
        "postings": s["postings"],
        "of": d["total_postings"],
        "required_ratio": round(s["required"] / s["postings"], 2) if s["postings"] else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="추천 이유 v1 데모")
    ap.add_argument("--skills", nargs="+", default=DEFAULT_SKILLS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="  [로그] %(message)s")

    mode = "mock" if is_mock() else "bedrock"
    print(f"프롬프트 {PROMPT_VERSION} / 모드 {mode} / 기술 {args.skills}")
    if mode == "mock":
        print("가짜 모드: data/sample/mock_bedrock_recommend.json 의 응답입니다.")

    total = {}
    records, errors = [], []
    for skill in args.skills:
        demand = demand_of(skill)
        print(f"\n■ {skill}: 공고 {demand['of']}건 중 {demand['postings']}건, 필수 {demand['required_ratio']:.0%} (건수 가정)")
        try:
            rec, result = build_recommendation(skill, demand)
        except BedrockJSONError as e:
            add_usage(total, e.usage)
            errors.append({"skill": skill, "error": str(e), "usage": e.usage})
            print(f"  ! 오류: {e}")
            continue
        if result is None:
            print("  연결된 과정이 없어 LLM 을 부르지 않았습니다.")
        else:
            add_usage(total, result.usage)
            print(f"  시도 {result.attempts}회, 토큰 {result.usage['totalTokens']}"
                  f" (입력 {result.usage['inputTokens']} / 출력 {result.usage['outputTokens']})")
        objectives = {c["title"]: c["objectives"] for c in candidate_courses(skill)}
        for c in rec["courses"]:
            print(f"  - [{c['match']}] {c['title']} ({c['level']}, 선수지식: {c['prerequisites'][:40]})")
            print(f"      이유: {c['reason']}")
            print(f"      학습 목표: {' / '.join(objectives[c['title']])[:200]}")
        records.append({
            "recommendation": rec,
            "attempts": result.attempts if result else 0,
            "usage": result.usage if result else {},
            "calls": list(result.calls) if result else [],
        })

    print(f"\n합계 토큰 {total.get('totalTokens', 0)} (입력 {total.get('inputTokens', 0)} / 출력 {total.get('outputTokens', 0)})"
          f" / 오류 {len(errors)}건")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{PROMPT_VERSION}_{mode}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(
        json.dumps({"prompt": PROMPT_VERSION, "mode": mode, "records": records, "errors": errors, "usage": total},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"원본 저장: {out.relative_to(ROOT)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
