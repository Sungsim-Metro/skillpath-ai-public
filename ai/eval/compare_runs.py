"""run_raw 결과 두 개 비교 (#21). 정답이 없는 개선용 공고에서 프롬프트 버전을 비교할 때 쓴다.

실행: .venv\\Scripts\\python -m ai.eval.compare_runs outputs/eval/raw_run_A.json outputs/eval/raw_run_B.json

- 공고 본문과 근거 문장은 출력하지 않는다. 기술 이름과 숫자만 보여준다.
- 품질 의심(묶음·범주)은 최종 결과 이름으로 다시 센다. 그래서 llm_quality 가 없는 예전 결과 파일과도 비교된다.
- 의심 표시는 사람이 확인할 후보를 고르는 용도다. 정답이 아니다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai.eval.run_raw import _BUNDLE, _CATEGORY
from pipeline.aliases import normalize_skill


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def metrics(run: dict) -> dict:
    skills = [s for r in run["rows"] for s in r["extraction"]["skills"]]
    names = [s["normalized"] for s in skills]
    unknown = [n for n in names if normalize_skill(n) is None]
    tokens = sum(r["usage"].get("totalTokens", 0) for r in run["rows"])
    return {
        "기술 수": len(skills),
        "사전 안": sum(normalize_skill(n) is not None for n in names),
        "uncertain": sum(s["confidence"] == "uncertain" for s in skills),
        "묶음 의심": [n for n in names if _BUNDLE.search(n)],
        "범주 의심": [n for n in unknown if not _BUNDLE.search(n) and _CATEGORY.search(n)],
        "사전 밖 이름": sorted(set(unknown)),
        "토큰": tokens,
        "오류": sum(bool(r["error"]) for r in run["rows"]),
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    a, b = load(sys.argv[1]), load(sys.argv[2])
    la, lb = a.get("prompt", "A"), b.get("prompt", "B")
    ma, mb = metrics(a), metrics(b)
    print(f"A = {la} ({Path(sys.argv[1]).name})\nB = {lb} ({Path(sys.argv[2]).name})\n")
    print(f"| 지표 | {la} | {lb} |\n| --- | --- | --- |")
    for k in ("기술 수", "사전 안", "uncertain", "토큰", "오류"):
        va, vb = ma[k], mb[k]
        if k in ("사전 안", "uncertain"):
            va, vb = f"{va} ({va / max(ma['기술 수'], 1):.0%})", f"{vb} ({vb / max(mb['기술 수'], 1):.0%})"
        print(f"| {k} | {va} | {vb} |")
    for k in ("묶음 의심", "범주 의심"):
        print(f"| {k} | {len(ma[k])} | {len(mb[k])} |")
    print(f"| 사전 밖 이름 (종류) | {len(ma['사전 밖 이름'])} | {len(mb['사전 밖 이름'])} |")
    for k in ("묶음 의심", "범주 의심"):
        print(f"\n{k}\n  {la}: {ma[k] or '-'}\n  {lb}: {mb[k] or '-'}")

    print("\n공고별로 바뀐 기술 (B 에만 + / A 에만 -)")
    rows_b = {r["id"]: r for r in b["rows"]}
    for ra in a["rows"]:
        rb = rows_b.get(ra["id"])
        if rb is None:
            print(f"  {ra['id']}: B 에 없음")
            continue
        na = {s["normalized"] for s in ra["extraction"]["skills"]}
        nb = {s["normalized"] for s in rb["extraction"]["skills"]}
        if na != nb:
            plus, minus = sorted(nb - na), sorted(na - nb)
            print(f"  {ra['id']} ({len(na)}→{len(nb)}): + {', '.join(plus) or '-'} / - {', '.join(minus) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
