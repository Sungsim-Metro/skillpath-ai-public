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
FIELDS = ("job", "action", "model", "rating", "left", "right", "picked", "tokens", "failed")
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


# 구조 이름 → (기술, 수요 숫자)를 받아 추천 1건(③ 형식)을 돌려주는 함수. 새 구조는 여기에 한 줄을 더하고 AB_STRUCTURES 에 이름을 적는다.
STRUCTURES: dict[str, Callable[[str, dict], dict]] = {"single": _single, "reviewed": _reviewed}
DEFAULT_STRUCTURE = "reviewed"


def structures() -> list[str]:
    """A/B 에 쓰는 구조 이름. 등록된 이름이 2개 이상일 때만 켜진다."""
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
    return bool(models() or structures())


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
        rows.extend(json_numbers(item) for item in page.get("Items", []))
        start = page.get("LastEvaluatedKey")
        if not start:
            return rows
