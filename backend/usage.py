"""토큰·비용 집계 (2026-11-16 결정). ai/usage.py 가 남긴 호출 기록을 읽어 단계별로 더한다. 전부 코드로 계산한다.

비용 = 입력 토큰 ÷ 1000 × 입력 단가 + 출력 토큰 ÷ 1000 × 출력 단가. 단가는 data/pricing.json 에 사람이 적는다.
단가가 없는 모델의 호출은 토큰만 세고 비용에는 넣지 않으며, 그런 호출이 몇 건인지 함께 돌려준다(비용을 낮게 보이게 하지 않으려고).
가짜 모드 호출은 토큰이 0 이라 따로 센다.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from ai.usage import log_path

PRICING = Path(__file__).resolve().parent.parent / "data" / "pricing.json"
STAGE_LABEL = {
    "extract": "기술 추출", "recommend": "추천 이유", "review": "추천 검증", "judge": "평가 채점",
    "coffeechat": "커피챗 초안", "chat_intake": "상담 발화 분석", "agent": "상담 에이전트", "embedding": "임베딩",
}


def read_rows(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # 쓰다 끊긴 줄은 건너뛴다
        if _valid(row):
            rows.append(row)
    return rows


def _valid(row) -> bool:
    """집계에 쓸 수 있는 줄인가. 칸이 빠졌거나 형식이 다른 줄은 세지 않는다(화면이 한 줄 때문에 멈추지 않게)."""
    return (isinstance(row, dict) and isinstance(row.get("ts"), str) and isinstance(row.get("stage"), str)
            and all(type(row.get(k)) is int and row[k] >= 0 for k in ("inputTokens", "outputTokens")))


def load_pricing() -> dict:
    return json.loads(PRICING.read_text(encoding="utf-8"))


def cost_of(row: dict, pricing: dict) -> float | None:
    """호출 1건의 비용. 그 모델의 단가가 없으면 None."""
    model = row.get("model", "")
    price = pricing.get("models", {}).get(model) if isinstance(model, str) else None
    if not isinstance(price, dict) or not all(isinstance(price.get(k), (int, float)) for k in ("input_per_1k", "output_per_1k")):
        return None  # 단가가 없거나 한쪽만 적혔거나 숫자가 아니면 계산하지 않는다
    return row["inputTokens"] / 1000 * price["input_per_1k"] + row["outputTokens"] / 1000 * price["output_per_1k"]


def summarize(rows: list[dict] | None = None, pricing: dict | None = None) -> dict:
    rows = read_rows() if rows is None else rows
    pricing = load_pricing() if pricing is None else pricing
    real = [r for r in rows if not r.get("mock")]

    def total(group: list[dict]) -> dict:
        costs = [cost_of(r, pricing) for r in group]
        input_tokens = sum(r["inputTokens"] for r in group)
        output_tokens = sum(r["outputTokens"] for r in group)
        return {
            "calls": len(group),
            "retries": sum(isinstance(r.get("attempt"), int) and r["attempt"] > 1 for r in group),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": round(sum(c for c in costs if c is not None), 6),
            "unpriced_calls": sum(c is None for c in costs),
        }

    stages = sorted({r["stage"] for r in real})
    days = sorted({r["ts"][:10] for r in real})
    session_groups = defaultdict(list)
    for row in real:
        session_id = row.get("session_id")
        if isinstance(session_id, str) and re.fullmatch(r"[0-9a-f]{32}", session_id):
            session_groups[session_id].append(row)
    by_session = [
        {"session_id": session_id, "last": max(r["ts"] for r in group), **total(group)}
        for session_id, group in session_groups.items()
    ]
    by_session.sort(key=lambda session: (session["last"], session["session_id"]), reverse=True)
    return {
        "total": total(real),
        "by_session": by_session,
        "by_stage": [{"stage": s, "label": STAGE_LABEL.get(s, s), **total([r for r in real if r["stage"] == s])} for s in stages],
        "by_day": [{"day": d, **total([r for r in real if r["ts"][:10] == d])} for d in days],
        "mock_calls": len(rows) - len(real),
        "models": sorted({r["model"] if isinstance(r.get("model"), str) else "" for r in real}),
        "unpriced_models": sorted({r["model"] if isinstance(r.get("model"), str) else "" for r in real if cost_of(r, pricing) is None}),
        "first": min(r["ts"] for r in real) if real else None,
        "last": max(r["ts"] for r in real) if real else None,
        "pricing": {k: pricing.get(k) for k in ("source", "checked", "currency")},
        "priced_models": sorted(pricing.get("models", {})),
    }
