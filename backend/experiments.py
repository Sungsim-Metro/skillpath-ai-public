"""학습 상담의 비교 실험 두 가지와 그 기록 (2026-11-19 회의 결정, 사용자 테스트용).

실험 1 (모델): 메시지마다 EXPERIMENT_MODELS 중 하나를 무작위로 골라 그 턴의 모든 모델 호출에 쓴다. 사용자는 답에 좋아요·별로예요를 누른다.
실험 2 (구조): 과정 추천을 서로 다른 추천 구조 두 개로 만들어 A/B 로 보여 주고 더 나은 쪽을 고르게 한다.
둘 다 설정이 비어 있으면 꺼지고 상담은 전과 똑같이 동작한다. 무작위 배정과 집계는 코드가 한다.

기록에는 세션 ID, 턴 ID, 직무, 요청 종류, 모델, 평가, 구조 이름과 선택, 토큰 수만 남긴다.
사용자 발화, 답변 내용, 닉네임은 남기지 않는다(대화 기록을 저장하지 않는 원칙).
실제 모드는 DynamoDB(DYNAMODB_FEEDBACK_TABLE, 키 session_id + turn_id), 가짜 모드는 로컬 파일(FEEDBACK_LOG, 기본 outputs/feedback/feedback.jsonl).
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Callable

from backend import api

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "outputs" / "feedback" / "feedback.jsonl"
KST = timezone(timedelta(hours=9))
# 기록에 넣을 수 있는 항목. 여기에 없는 이름은 저장하지 않는다(내용이 섞여 들어가지 않게)
FIELDS = ("job", "action", "model", "rating", "left", "right", "picked", "tokens", "failed", "turn_tokens")
RATINGS = ("up", "down")
TIE = "tie"
_lock = Lock()


def _names(variable: str) -> list[str]:
    return list(dict.fromkeys(name.strip() for name in os.getenv(variable, "").split(",") if name.strip()))


# ---------- 실험 1: 모델 ----------

def models() -> list[str]:
    """무작위로 배정할 모델 ID 목록. 가짜 모드에서는 모델을 부르지 않으므로 빈 목록이다."""
    return [] if api.is_mock() else _names("EXPERIMENT_MODELS")


def pick_model(rng: random.Random | None = None) -> str | None:
    """이번 메시지에 쓸 모델. 실험이 꺼져 있으면 None(기본 모델을 쓴다)."""
    candidates = models()
    return (rng or random).choice(candidates) if candidates else None


# ---------- 실험 2: 추천 구조 ----------

def _single(skill: str, demand: dict) -> dict:
    """한 번에 쓰기: 모델 호출 한 번으로 추천 이유를 쓴다(코드 검사만)."""
    from ai.recommend import build_recommendation

    return build_recommendation(skill, demand)[0]


def _reviewed(skill: str, demand: dict) -> dict:
    """쓰기 → 검토 → 고치기: 별도 호출이 초안을 학습 목표로 검토하고 한 번 고친다(지금 상담의 기본)."""
    from ai.recommend import build_recommendation

    return build_recommendation(skill, demand, review=True)[0]


# 과정 카드 비교에 쓰는 구조는 (기술, 수요 숫자)를 받아 추천 1건(③ 형식)을 돌려준다.
STRUCTURES: dict[str, Callable[[str, dict], dict]] = {"single": _single, "reviewed": _reviewed}
DEFAULT_STRUCTURE = "reviewed"
# 상담 구조는 사용자 메시지를 받는 시점에 선택한다. 값은 backend.chat.handle_message의 architecture다.
CHAT_STRUCTURES = {"3": "supervised"}


def chat_architecture() -> str:
    """AB_STRUCTURES에 상담 구조 하나만 지정하면 그 구조로 매 턴을 처리한다."""
    names = _names("AB_STRUCTURES")
    if len(names) == 1:
        return CHAT_STRUCTURES.get(names[0], "screen")
    return "screen"


def chat_rival() -> str | None:
    """과정 추천 턴에서 기본 구조와 나란히 비교할 상담 구조 이름. 꺼져 있으면 None.

    AB_STRUCTURES 에 기본 구조(reviewed)와 상담 구조(3)를 함께 적으면 켜진다(예: `reviewed,3`).
    상담 구조 하나만 적으면 chat_architecture 가 그 구조로 모든 턴을 처리하고 비교는 하지 않는다.
    """
    names = _names("AB_STRUCTURES")
    if DEFAULT_STRUCTURE not in names:
        return None
    return next((name for name in names if name in CHAT_STRUCTURES), None)


def structures() -> list[str]:
    """과정 카드 A/B에 쓸 추천 구조 이름. 두 개 이상 등록되어야 투표를 연다."""
    names = [name for name in _names("AB_STRUCTURES") if name in STRUCTURES]
    return names if len(names) >= 2 else []


def pick_pair(rng: random.Random | None = None) -> tuple[str, str] | None:
    """이번 추천에서 비교할 구조 두 개를 무작위 순서(왼쪽, 오른쪽)로 고른다. 꺼져 있으면 None."""
    names = structures()
    if not names:
        return None
    left, right = (rng or random).sample(names, 2)
    return left, right


def recommend_with(structure: str, job_category: str, gap_skills: list[str]) -> list[dict]:
    """지정한 구조로 기술마다 추천을 만든다. 한 기술이 검사에 실패하면 그 기술만 빼고, 전부 실패하면 그 오류를 낸다."""
    from ai.bedrock import BedrockJSONError

    build = STRUCTURES[structure]
    out, failed = [], None
    for skill, demand in api.demand_pairs(job_category, gap_skills):
        try:
            out.append(build(skill, demand))
        except (BedrockJSONError, KeyError, TypeError, ValueError) as exc:
            failed = exc
    if failed is not None and not any(rec["courses"] for rec in out):
        raise failed
    return out


def active() -> bool:
    """실험이 하나라도 켜져 있는가. 꺼져 있으면 화면은 평가를 받지 않는다."""
    return bool(models() or structures() or chat_rival())


# ---------- 기록 ----------

def _log_path() -> Path:
    return Path(os.getenv("FEEDBACK_LOG") or DEFAULT_LOG)


def _table():
    from backend.dynamodb import _resource

    name = os.getenv("DYNAMODB_FEEDBACK_TABLE", "").strip()
    if not name:
        raise ValueError("DYNAMODB_FEEDBACK_TABLE을 설정하세요.")
    return _resource().Table(name)


def _clean(fields: dict) -> dict:
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"기록할 수 없는 항목: {', '.join(sorted(unknown))}")
    if "rating" in fields and fields["rating"] not in RATINGS:
        raise ValueError("rating 은 up 또는 down 입니다.")
    return {key: value for key, value in fields.items() if value is not None}


def save_feedback(session_id: str, turn_id: str, **fields) -> None:
    """한 턴의 기록에 항목을 더하거나 고친다. 같은 턴을 여러 번 저장하면 나중 값이 남는다."""
    if not session_id or not turn_id:
        raise ValueError("session_id 와 turn_id 가 필요합니다.")
    values = {**_clean(fields), "ts": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S")}
    if api.is_mock():
        path = _log_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"session_id": session_id, "turn_id": turn_id, **values}, ensure_ascii=False) + "\n")
        return
    names = {f"#f{i}": key for i, key in enumerate(values)}
    _table().update_item(
        Key={"session_id": session_id, "turn_id": turn_id},
        UpdateExpression="SET " + ", ".join(f"{alias} = :v{i}" for i, alias in enumerate(names)),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues={f":v{i}": values[key] for i, key in enumerate(values)},
    )


def load_feedback() -> list[dict]:
    """모든 턴의 기록(턴마다 한 건으로 합친 것)."""
    if api.is_mock():
        path = _log_path()
        merged: dict[tuple[str, str], dict] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    merged.setdefault((row["session_id"], row["turn_id"]), {}).update(row)
                except (ValueError, KeyError, TypeError):
                    continue
        return list(merged.values())
    from backend.dynamodb import json_numbers

    table, rows, start = _table(), [], None
    while True:
        page = table.scan(**({"ExclusiveStartKey": start} if start else {}))
        rows.extend(json_numbers(item) for item in page.get("Items", []) if item.get("session_id") != DECISION_KEY)
        start = page.get("LastEvaluatedKey")
        if not start:
            return rows


# ---------- 집계 (전부 코드로 센다) ----------

MIN_RATINGS = 20   # 모델마다 평가가 이만큼 모이기 전의 결론은 참고용으로만 표시한다(팀 결정값)
TOLERANCE = 0.05   # 좋아요 비율이 가장 높은 모델과 이 차이 안이면 "충분한 모델"로 본다(팀 결정값)


def _turn_cost(row: dict, pricing: dict) -> float | None:
    price = pricing.get("models", {}).get(row.get("model", ""))
    tokens = row.get("turn_tokens")
    if not isinstance(price, dict) or not isinstance(tokens, dict):
        return None
    return tokens.get("input", 0) / 1000 * price["input_per_1k"] + tokens.get("output", 0) / 1000 * price["output_per_1k"]


def summarize_models(rows: list[dict] | None = None, pricing: dict | None = None) -> list[dict]:
    """모델별 턴 수, 평가, 검사 실패, 턴당 평균 토큰과 추정 비용. 좋아요 비율은 평가한 턴 중의 비율이다."""
    from backend.usage import load_pricing

    rows = load_feedback() if rows is None else rows
    pricing = load_pricing() if pricing is None else pricing
    out = []
    for model in sorted({r["model"] for r in rows if isinstance(r.get("model"), str) and r["model"]}):
        group = [r for r in rows if r.get("model") == model]
        measured = [r for r in group if isinstance(r.get("turn_tokens"), dict)]
        costs = [c for c in (_turn_cost(r, pricing) for r in measured) if c is not None]
        up = sum(r.get("rating") == "up" for r in group)
        down = sum(r.get("rating") == "down" for r in group)
        failed = sum(bool(r.get("failed")) for r in group)
        out.append({
            "model": model, "turns": len(group), "rated": up + down, "up": up, "down": down,
            "up_ratio": round(up / (up + down), 3) if up + down else None,
            "failed": failed, "failed_ratio": round(failed / len(group), 3),
            "avg_input_tokens": round(sum(r["turn_tokens"].get("input", 0) for r in measured) / len(measured)) if measured else None,
            "avg_output_tokens": round(sum(r["turn_tokens"].get("output", 0) for r in measured) / len(measured)) if measured else None,
            "avg_cost": round(sum(costs) / len(costs), 6) if costs else None,
            "currency": pricing.get("currency"),
        })
    return out


def summarize_structures(rows: list[dict] | None = None) -> list[dict]:
    """추천 구조별 노출 수(A/B 로 보여 주고 선택까지 받은 횟수), 선택 수, 선택률, 추천 한 번의 평균 토큰."""
    rows = load_feedback() if rows is None else rows
    picked = [r for r in rows if r.get("picked") and r.get("left") and r.get("right")]
    out = []
    for name in sorted({side for r in picked for side in (r["left"], r["right"])}):
        shown = [r for r in picked if name in (r["left"], r["right"])]
        wins = sum(r["picked"] == name for r in shown)
        ties = sum(r["picked"] == TIE for r in shown)
        tokens = [r["tokens"][name] for r in shown if isinstance(r.get("tokens"), dict) and isinstance(r["tokens"].get(name), dict)]
        out.append({
            "structure": name, "shown": len(shown), "picked": wins, "ties": ties,
            "pick_ratio": round(wins / len(shown), 3) if shown else None,
            "avg_input_tokens": round(sum(t.get("input", 0) for t in tokens) / len(tokens)) if tokens else None,
            "avg_output_tokens": round(sum(t.get("output", 0) for t in tokens) / len(tokens)) if tokens else None,
        })
    return out


def decide_model(summary: list[dict], *, min_ratings: int = MIN_RATINGS, tolerance: float = TOLERANCE) -> dict:
    """평가와 비용으로 쓸 모델을 고른다.

    규칙: 좋아요 비율이 가장 높은 모델과의 차이가 tolerance 안인 모델들 가운데 턴당 추정 비용이 가장 낮은 모델.
    비율이나 비용을 알 수 없는 모델은 후보에서 뺀다. 모델마다 평가가 min_ratings 건이 안 되면 status 가 "provisional"(참고용)이다.
    """
    usable = [m for m in summary if m["up_ratio"] is not None and m["avg_cost"] is not None]
    rule = {"min_ratings": min_ratings, "tolerance": tolerance}
    if len(usable) < 2:
        return {"status": "insufficient", "model": None, "rule": rule,
                "reason": "평가와 비용이 모두 있는 모델이 2개 미만이라 비교할 수 없습니다."}
    best = max(m["up_ratio"] for m in usable)
    good_enough = [m for m in usable if m["up_ratio"] >= best - tolerance]
    chosen = min(good_enough, key=lambda m: (m["avg_cost"], -m["up_ratio"], m["model"]))
    priciest = max(usable, key=lambda m: m["avg_cost"])
    saving = round(1 - chosen["avg_cost"] / priciest["avg_cost"], 3) if priciest["avg_cost"] else 0
    enough = all(m["rated"] >= min_ratings for m in usable)
    return {
        "status": "decided" if enough else "provisional", "model": chosen["model"], "rule": rule,
        "up_ratio": chosen["up_ratio"], "best_up_ratio": best, "avg_cost": chosen["avg_cost"],
        "saving_vs_priciest": saving, "compared_with": priciest["model"],
        "reason": (f"좋아요 비율 {chosen['up_ratio']:.0%}(최고 {best:.0%}, 허용 차이 {tolerance * 100:.0f}%p 안)이고 "
                   f"턴당 추정 비용이 가장 낮습니다. 가장 비싼 모델보다 {saving:.0%} 적게 듭니다."
                   + ("" if enough else f" 모델별 평가가 {min_ratings}건에 못 미쳐 참고용입니다.")),
    }


# ---------- 결정 기록 ----------

DECISION_KEY = "decision"  # 평가 테이블에서 결정 기록이 쓰는 session_id. 대화 세션 ID(32자리 16진수)와 겹치지 않는다


def _decision_path() -> Path:
    return _log_path().with_name("decisions.jsonl")


def save_decision(models: list[dict], decision: dict) -> dict:
    """그때의 모델별 집계와 고른 모델을 한 건으로 남긴다. 나중에 무엇을 보고 골랐는지 다시 볼 수 있다."""
    record = {"ts": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S"), "decision": decision, "models": models}
    payload = json.dumps(record, ensure_ascii=False)
    if api.is_mock():
        path = _decision_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
    else:
        _table().put_item(Item={"session_id": DECISION_KEY, "turn_id": record["ts"], "payload": payload})
    return record


def load_decisions() -> list[dict]:
    """남긴 결정 기록(최근 것이 앞)."""
    if api.is_mock():
        path = _decision_path()
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    else:
        from boto3.dynamodb.conditions import Key

        items = _table().query(KeyConditionExpression=Key("session_id").eq(DECISION_KEY)).get("Items", [])
        lines = [item["payload"] for item in items]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return sorted(records, key=lambda r: r.get("ts", ""), reverse=True)
