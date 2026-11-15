"""사전에 없는 기술 표현 → 가장 가까운 대표 이름 후보 (#13).

임베딩 유사도는 **후보를 찾는 데만** 쓴다. 이 함수는 표현을 합치지 않는다(Java ≠ JavaScript).
점수가 THRESHOLD 에 못 미치면 uncertain 으로 표시하고, 사전에 더할지는 사람이 정한다.
한글 음역(파이썬→Python, 자바→Java)은 약해서 상위 3개를 같이 보여준다. hit@3 은 0.85 다.

사용:
  python -m pipeline.suggest "아마존 클라우드" Kafka     # 후보 보기
  python -m pipeline.suggest --eval                       # 사전의 별칭 하나씩 빼고 되찾는지 측정(실제 모드 권장)
"""
from __future__ import annotations

import json
import sys

from pipeline.aliases import ALIAS_FILE, _key
from pipeline.embed import cosine, embed, embed_many, is_mock, usage

# 질의와 이름 앞에 붙이는 설명. 없을 때 hit@1 0.74, hit@3 0.81 → 붙이면 0.77, 0.85 (실제 Titan, 2026-11-15)
PREFIX = "IT 기술 이름: "
# 이 점수 이상이면 candidate. 별칭 81개를 하나씩 빼고 시험했을 때 0.70 부터 틀린 후보가 0개이고
# 사전에 없는 말(JavaScript→Java 0.66 등)도 후보로 올라오지 않았다. 그 아래는 uncertain 이다. 가짜 모드의 점수에는 의미가 없다.
THRESHOLD = 0.70


def _names(exclude: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """(이름, 대표 이름) 쌍. 대표 이름과 별칭 모두. exclude 는 정규화한 이름 집합."""
    out = []
    for canon, aliases in json.loads(ALIAS_FILE.read_text(encoding="utf-8")).items():
        for name in [canon, *aliases]:
            if _key(name) not in exclude:
                out.append((name, canon))
    return out


def suggest(raw: str, top: int = 3, exclude: frozenset[str] = frozenset()) -> list[dict]:
    """대표 이름 후보 상위 top 개. 이미 사전에 있는 표현이어도 후보를 돌려준다(exclude 로 뺄 수 있다).

    반환: [{"canonical", "score", "matched", "confidence"}], confidence 는 "candidate" 또는 "uncertain".
    """
    pairs = _names(exclude)
    q = embed(PREFIX + raw)
    best: dict[str, tuple[float, str]] = {}
    for (name, canon), vec in zip(pairs, embed_many([PREFIX + n for n, _ in pairs])):
        score = cosine(q, vec)
        if canon not in best or score > best[canon][0]:
            best[canon] = (score, name)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:top]
    return [{"canonical": c, "score": round(s, 3), "matched": n,
             "confidence": "candidate" if s >= THRESHOLD else "uncertain"} for c, (s, n) in ranked]


def evaluate() -> None:
    """사전의 별칭을 하나씩 빼고, 대표 이름을 되찾는지(hit@1, hit@3)와 점수 분포를 본다."""
    data = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
    hits1 = hits3 = total = 0
    pos, misses = [], []
    for canon, aliases in data.items():
        for alias in aliases:
            got = suggest(alias, top=3, exclude=frozenset({_key(alias)}))
            names = [g["canonical"] for g in got]
            total += 1
            hits1 += names[:1] == [canon]
            hits3 += canon in names
            pos.append(got[0]["score"] if got and got[0]["canonical"] == canon else None)
            if names[:1] != [canon]:
                misses.append((alias, canon, got[0]["canonical"], got[0]["score"]))
    ok = [p for p in pos if p is not None]
    print(f"모드: {'가짜(의미 없음)' if is_mock() else '실제 Titan'} / 별칭 {total}개")
    print(f"hit@1 {hits1}/{total} = {hits1/total:.2f}   hit@3 {hits3}/{total} = {hits3/total:.2f}")
    if ok:
        print(f"맞힌 것의 1위 점수: 최소 {min(ok):.2f}, 평균 {sum(ok)/len(ok):.2f}")
    for alias, canon, got, score in misses[:15]:
        print(f"  못 맞힘: {alias!r} (정답 {canon}) → {got} {score}")
    # 사전에 없는 말(음성 예시)이 얼마나 높게 나오는지: 기준값을 정하는 데 쓴다
    negatives = ["Rust", "Kafka", "JavaScript", "Ruby", "PHP", "Redis", "Elasticsearch", "Ansible", "TypeScript", "Excel"]
    print("사전에 없는 말의 1위 점수:")
    for n in negatives:
        g = suggest(n, top=1)[0]
        print(f"  {n:14s} → {g['canonical']:12s} {g['score']}")
    print("사용량:", usage())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args == ["--eval"]:
        evaluate()
        return
    for raw in args:
        print(raw, "→", json.dumps(suggest(raw), ensure_ascii=False))
    print("사용량:", usage())


if __name__ == "__main__":
    main()
