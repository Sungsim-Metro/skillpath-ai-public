"""교육과정 검색(search_courses) 평가 (#14).

정답 초안 ai/eval/search_gold.json 을 기준으로 기술마다 상위 3개 결과를 채점한다. 점수는 모두 코드가 계산한다.
- hit@3, hit@5: 정답(relevant) 과정이 상위 3개, 5개 안에 있는가 (정답이 있는 기술만 센다)
- precision@3: 결과 중 정답 비율의 평균 (정답이 있는 기술만)
- coverage: 정답이 있는 기술 중 결과가 하나라도 나온 비율
- bad: 정답도 관련(related)도 아닌 결과의 수 (틀린 추천)
- false_positive: 정답과 관련이 모두 없는(비어야 하는) 기술에 결과가 나온 수

실행 (저장소 루트에서):
  python -X utf8 -m pipeline.eval_search                     # 규칙/키워드/임베딩/섞은 방식/최종 비교표(키워드 이후는 실제 모드)
  BEDROCK_MOCK=0 python -X utf8 -m pipeline.eval_search --detail final   # 기술별 결과까지
정답은 사람이 만든 초안이고 30개 남짓이라 통계적 주장은 못 한다. 개선 전후를 같은 세트로 비교하는 용도다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.search import MODES, _courses, _usable, search_courses

GOLD = Path(__file__).resolve().parent.parent / "ai" / "eval" / "search_gold.json"


def load_gold() -> list[dict]:
    """정답 초안. 없는 과정이나 추천할 수 없는 과정(종료 예정, 학습 목표 없음)이 들어 있으면 ValueError."""
    gold = json.loads(GOLD.read_text(encoding="utf-8"))["skills"]
    courses = _courses()
    for g in gold:
        for cid in g["relevant"] + g["related"]:
            if cid not in courses:
                raise ValueError(f"{g['skill']}: 과정 {cid} 가 courses.csv 에 없습니다")
            if g["relevant"].count(cid) and not _usable(courses[cid]):
                raise ValueError(f"{g['skill']}: 정답 과정 {cid} 는 추천할 수 없는 과정입니다(종료 예정이거나 학습 목표 없음)")
    return gold


def evaluate(search=search_courses, k: int = 5) -> dict:
    """기술마다 채점하고 합계를 돌려준다. search 는 (skill, k) → [{course_id, ...}].
    상위 k(5)개로 hit@5 를, 앞의 3개로 나머지 지표를 낸다(서비스는 추천을 상위 3개로 자른다)."""
    rows = []
    for g in load_gold():
        found5 = [f["course_id"] for f in search(g["skill"], k)]
        found = found5[:3]
        good, ok = set(g["relevant"]), set(g["related"])
        hits = [c for c in found if c in good]
        rows.append({
            "skill": g["skill"],
            "expect": "정답" if good else ("관련만" if ok else "비어야 함"),
            "found": found,
            "hit3": bool(hits) if good else None,
            "hit5": any(c in good for c in found5) if good else None,
            "precision": len(hits) / len(found) if good and found else (0.0 if good else None),
            "bad": sum(1 for c in found if c not in good and c not in ok),
            "false_positive": bool(found) if not good and not ok else None,
        })
    with_gold = [r for r in rows if r["hit3"] is not None]
    empty = [r for r in rows if r["false_positive"] is not None]
    summary = {
        "skills": len(rows),
        "hit@3": sum(r["hit3"] for r in with_gold) / len(with_gold),
        "hit@5": sum(r["hit5"] for r in with_gold) / len(with_gold),
        "precision@3": sum(r["precision"] for r in with_gold) / len(with_gold),
        "coverage": sum(1 for r in with_gold if r["found"]) / len(with_gold),
        "bad": sum(r["bad"] for r in rows),
        "false_positive": f"{sum(r['false_positive'] for r in empty)}/{len(empty)}",
    }
    return {"rows": rows, "summary": summary}


def print_report(result: dict) -> None:
    print("| 기술 | 정답 | 결과 수 | hit@3 | hit@5 | precision | 틀린 결과 |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    mark = lambda v: "-" if v is None else ("O" if v else "X")
    for r in result["rows"]:
        prec = "-" if r["precision"] is None else f"{r['precision']:.2f}"
        fp = " (결과가 나옴)" if r["false_positive"] else ""
        print(f"| {r['skill']} | {r['expect']} | {len(r['found'])}{fp} | {mark(r['hit3'])} | {mark(r['hit5'])} | {prec} | {r['bad']} |")


def print_comparison(modes: list[str]) -> None:
    """방식별 비교표(2026-11-16 결정 9번). 같은 정답 세트로 방식만 바꿔 채점한다."""
    print("| 방식 | hit@3 | hit@5 | precision@3 | coverage | 틀린 결과 | 비어야 하는데 나온 기술 |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for m in modes:
        s = evaluate(lambda skill, k, m=m: search_courses(skill, k, mode=m))["summary"]
        print(f"| {m} | {s['hit@3']:.2f} | {s['hit@5']:.2f} | {s['precision@3']:.2f} | {s['coverage']:.2f} | {s['bad']} | {s['false_positive']} |")


if __name__ == "__main__":
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES, help="비교할 방식")
    ap.add_argument("--detail", choices=MODES, help="이 방식의 기술별 결과도 출력")
    args = ap.parse_args()
    print_comparison(args.modes)
    if args.detail:
        print()
        print_report(evaluate(lambda skill, k: search_courses(skill, k, mode=args.detail)))
