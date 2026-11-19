"""LLM 채점과 사람 채점의 일치율, 점수 분포, 같은 글 두 번 채점의 잡음, 사람 채점 양식(#94). 계산은 전부 코드로 한다.

형섭의 feat/eval-judge(ai/eval/judge.py, 추천 이유 0·1·2 점 채점)에서 일치율·분포·잡음·양식 함수를 옮겨,
기준 이름과 점수 범위를 인자로 받게 일반화했다(상담 답 채점은 1~5 점). 출처는 함수마다 적는다.
채점 대상 만들기, 사람이 볼 항목 고르기, 채점용 정답지 세기는 옮기지 않았다(상담 답 채점 기준을 만들 때 정한다).

일치율은 세 가지를 같이 본다. 문항이 적으면 kappa 가 크게 흔들리므로 하나만 보지 않는다.
- 정확 일치율: LLM 과 사람 점수가 같은 비율
- ±1 일치율: 점수 차이가 1 이하인 비율(1~5 점에서는 1점 차이가 흔하다)
- 가중 Cohen's kappa(이차 가중치): 우연히 맞을 비율을 뺀 일치도. 차이가 클수록 더 크게 벌점을 준다
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Optional

SCORES = (1, 2, 3, 4, 5)
KAPPA_WEIGHT = "quadratic"  # 결과 파일에 적는 가중치 종류
LOW_MAX = 3                 # 이 점수 이하를 "낮게 준 항목"으로 본다


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 validate_scores 를 기준 이름·점수 범위 인자로 일반화)
def validate_scores(data: dict, item_ids: list[str], criteria: Iterable[str], scores: Iterable[int] = SCORES) -> None:
    """채점 응답 형식 검사. 틀리면 ValueError(converse_json 이 한 번 재시도한다).

    {"scores": [{"item_id", <기준>: 정수, "note": 문자열|null}]} 이고, item_id 순서가 입력과 같아야 한다.
    """
    allowed = tuple(scores)
    rows = data.get("scores")
    if not isinstance(rows, list):
        raise ValueError('"scores" 목록이 없습니다')
    got = [r.get("item_id") if isinstance(r, dict) else None for r in rows]
    if got != item_ids:
        raise ValueError(f"item_id 가 입력과 다릅니다: 입력 {item_ids}, 응답 {got}")
    for r in rows:
        for k in criteria:
            if type(r.get(k)) is not int or r[k] not in allowed:
                raise ValueError(f"{r['item_id']} 의 {k} 가 {r.get(k)!r} 입니다. {', '.join(map(str, allowed))} 중 하나여야 합니다")
        if r.get("note") is not None and not isinstance(r["note"], str):
            raise ValueError(f"{r['item_id']} 의 note 는 문자열이나 null 이어야 합니다")


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 distribution 을 기준·점수 범위 인자로 일반화)
def distribution(items: list[dict], criteria: Iterable[str], scores: Iterable[int] = SCORES) -> dict:
    """기준마다 점수별 항목 수. 평균만 보면 전부 같은 점수인 채점(변별력 없음)을 놓친다. items[i]["scores"] 를 센다."""
    allowed = tuple(scores)
    rows = [it for it in items if it.get("scores")]
    return {k: {str(v): sum(r["scores"][k] == v for r in rows) for v in allowed} for k in criteria}


def weighted_kappa(pairs: list[tuple[int, int]], scores: Iterable[int] = SCORES) -> Optional[float]:
    """이차 가중 Cohen's kappa. pairs = [(LLM 점수, 사람 점수)]. 계산할 수 없으면 None.

    kappa = 1 − Σ w·관찰 / Σ w·기대, w(i, j) = (i − j)² / (k − 1)². 기대 불일치가 0 이면(두 채점자가 모든 항목에
    같은 한 점수를 줄 때) 나눌 수 없어 None 이다. 항목이 없어도 None. 한쪽만 모든 항목에 같은 점수를 주면 0 이 된다
    (그래서 agreement 는 single_valued 로 따로 알린다).
    """
    cats = list(scores)
    k, n = len(cats), len(pairs)
    if n == 0 or k < 2:
        return None
    index = {c: i for i, c in enumerate(cats)}
    observed = [[0] * k for _ in range(k)]
    for a, b in pairs:
        observed[index[a]][index[b]] += 1
    rows = [sum(observed[i]) for i in range(k)]
    cols = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    seen = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k))
    chance = sum(weight[i][j] * rows[i] * cols[j] / n for i in range(k) for j in range(k))
    if chance == 0:
        return None
    return round(1 - seen / chance, 3)


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 agreement 를 1~5 점으로 일반화하고 ±1 일치율·가중 kappa 를 더함)
def agreement(llm: dict[str, dict], human: dict[str, dict], criteria: Iterable[str],
              scores: Iterable[int] = SCORES, low_max: int = LOW_MAX) -> dict:
    """기준마다 정확 일치율, ±1 일치율, 이차 가중 kappa, 평균 차이(LLM − 사람), 교차표, 사람이 낮게 준 항목을 LLM 도
    낮게 줬는지. 사람이 채운 칸만 센다. confusion[사람 점수][LLM 점수] = 항목 수.
    """
    allowed = tuple(scores)
    by_criterion = {}
    for k in criteria:
        pairs = [(llm[i][k], human[i][k]) for i in human if i in llm and human[i].get(k) is not None]
        n = len(pairs)
        low = [(a, b) for a, b in pairs if b <= low_max]
        by_criterion[k] = {
            "n": n,
            "exact": round(sum(a == b for a, b in pairs) / n, 2) if n else None,
            "within_one": round(sum(abs(a - b) <= 1 for a, b in pairs) / n, 2) if n else None,
            "kappa": weighted_kappa(pairs, allowed),
            "mean_diff": round(sum(a - b for a, b in pairs) / n, 2) if n else None,
            "confusion": {str(h): {str(m): sum(a == m and b == h for a, b in pairs) for m in allowed} for h in allowed},
            "human_low": len(low),
            "human_low_caught": sum(a <= low_max for a, _ in low),
            "single_valued": n > 1 and (len({a for a, _ in pairs}) == 1 or len({b for _, b in pairs}) == 1),
        }
    return {"kappa_weight": KAPPA_WEIGHT, "low_max": low_max, "scores": list(allowed), "by_criterion": by_criterion}


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 paired_change 를 single·debate 고정에서 두 라벨로 일반화)
def paired_change(items: list[dict], key: tuple[str, ...], label_field: str, label_a: str, label_b: str,
                  text_fields: tuple[str, ...], criteria: Iterable[str]) -> Optional[dict]:
    """같은 key 에 label_a·label_b 점수가 모두 있는 쌍을, 글이 같은 쌍과 바뀐 쌍으로 나눠 센다.

    글(text_fields)이 같은 쌍은 같은 글을 두 번 채점한 것이다. 거기서 난 점수 차이는 효과가 아니라 채점 잡음(noise)이다.
    효과는 글이 바뀐 쌍에서만 읽는다. 예: 같은 상담 답을 두 번 채점(잡음), 같은 질문에 대한 구조 A·B 의 답(효과).
    """
    crit = tuple(criteria)
    by: dict[tuple, dict] = {}
    for it in items:
        if it.get("scores"):
            by.setdefault(tuple(it[f] for f in key), {})[it[label_field]] = it
    pairs = [(v[label_a], v[label_b]) for v in by.values() if {label_a, label_b} <= set(v)]
    if not pairs:
        return None

    def total(it):
        return sum(it["scores"][k] for k in crit)

    def count(rows):
        return {"pairs": len(rows), "higher": sum(total(b) > total(a) for a, b in rows),
                "same": sum(total(b) == total(a) for a, b in rows), "lower": sum(total(b) < total(a) for a, b in rows)}

    changed = [(a, b) for a, b in pairs if any(a[f] != b[f] for f in text_fields)]
    identical = [(a, b) for a, b in pairs if (a, b) not in changed]
    return {**count(pairs), "changed": count(changed), "identical": count(identical),
            "noise": sum(total(a) != total(b) for a, b in identical)}


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 write_human_sheet. 고르기는 하지 않고 이미 고른 행을 받는다)
def write_human_sheet(path: Path, rows: list[dict], columns: list[str], criteria: Iterable[str]) -> list[str]:
    """사람 채점 양식(CSV, 엑셀용 utf-8-sig). rows 는 이미 고른 항목이다(고르는 규칙은 채점 기준과 함께 정한다).

    LLM 점수와 어느 구조의 답인지는 넣지 않는다(보고 따라 매기지 않게). 기준 칸과 memo 는 비워 둔다.
    columns 에 없는 행의 키는 넣지 않는다. 쓴 item_id 목록을 돌려준다.
    """
    crit = tuple(criteria)
    fieldnames = ["item_id", *[c for c in columns if c not in ("item_id", *crit, "memo")], *crit, "memo"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: r.get(c, "") for c in fieldnames if c not in (*crit, "memo")}, **{k: "" for k in crit}, "memo": ""})
    return [r["item_id"] for r in rows]


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 _decode 그대로)
def _decode(path: Path) -> list[str]:
    """엑셀의 "CSV UTF-8" 과 한국어 엑셀의 기본 CSV(cp949)를 모두 읽는다."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding).splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{Path(path).name} 을 읽지 못했습니다. 엑셀에서 'CSV UTF-8(쉼표로 분리)'로 저장해 주세요")


# 출처: 형섭 feat/eval-judge (ai/eval/judge.py 의 read_human_sheet 를 기준·점수 범위 인자로 일반화)
def read_human_sheet(path: Path, criteria: Iterable[str], scores: Iterable[int] = SCORES) -> dict[str, dict]:
    """사람이 채운 양식을 {item_id: {기준: 점수 또는 None}} 로 읽는다. 빈 칸은 None(세지 않음).

    엑셀이 숫자를 소수로 바꿔 저장한 "3.0" 도 받는다. item_id 가 없는 행(값이 있는데)·같은 item_id 두 번·범위 밖 값은 오류.
    """
    crit = tuple(criteria)
    allowed = {str(v): v for v in scores}
    name = Path(path).name
    out = {}
    for line, row in enumerate(csv.DictReader(_decode(path)), start=2):
        item_id = (row.get("item_id") or "").strip()
        if not item_id:
            if any((v or "").strip() for v in row.values()):
                raise ValueError(f"{name} {line}행에 item_id 가 없습니다")
            continue  # 엑셀이 끝에 붙인 빈 줄
        if item_id in out:
            raise ValueError(f"{name} {line}행: item_id {item_id} 가 두 번 나옵니다")
        got = {}
        for k in crit:
            cell = (row.get(k) or "").strip()
            if cell.endswith(".0"):
                cell = cell[:-2]
            if cell == "":
                got[k] = None
            elif cell in allowed:
                got[k] = allowed[cell]
            else:
                raise ValueError(f"{name} {line}행 {k} 칸이 {cell!r} 입니다. {', '.join(allowed)} 중 하나로 적어 주세요")
        out[item_id] = got
    return out
