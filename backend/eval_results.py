"""관리자 화면에 보여 줄 평가 결과를 읽는다. 계산은 하지 않고 평가 스크립트가 남긴 것을 그대로 옮긴다.

- 결과 파일: 평가 스크립트(ai/eval/)가 outputs/eval/*.json 에 쓴다. outputs/ 는 저장소에 올리지 않으므로,
  평가를 돌린 사람이 이 서버의 같은 폴더에 올려야 보인다. 없으면 빈 목록이다.
- 요약 표: ai/eval/IMPROVEMENT_LOG.md 의 "발표용 요약" 절(사람이 적은 표)을 그대로 돌려준다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "outputs" / "eval"
LOG = ROOT / "ai" / "eval" / "IMPROVEMENT_LOG.md"
SUMMARY_HEADING = "## 발표용 요약"
KST = timezone(timedelta(hours=9))


def _summaries(data: dict) -> list[dict]:
    """파일 하나에서 표로 보여 줄 행을 뽑는다. {"result": {이름: {"summary": {...}}}} 또는 {"result": {이름: {...}}} 형식."""
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return []
    rows = []
    for name, value in result.items():
        if not isinstance(value, dict):
            continue
        summary = value.get("summary") if isinstance(value.get("summary"), dict) else value
        rows.append({"이름": name, **{k: v for k, v in summary.items() if isinstance(v, (int, float, str)) or v is None}})
    return rows


def list_results(folder: Path = RESULT_DIR) -> list[dict]:
    """[{"file", "modified"(한국 시간), "cases", "rows"}] 최근 파일이 앞. 읽지 못한 파일은 rows 가 비고 error 가 붙는다."""
    out = []
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    for path in files:
        item = {"file": path.name, "cases": None, "rows": [],
                "modified": datetime.fromtimestamp(path.stat().st_mtime, KST).strftime("%Y-%m-%d %H:%M")}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            item["rows"] = _summaries(data)
            cases = data.get("cases") if isinstance(data, dict) else None
            item["cases"] = len(cases) if isinstance(cases, list) else cases if isinstance(cases, int) else None
        except (OSError, ValueError) as exc:
            item["error"] = type(exc).__name__
        out.append(item)
    return out


def summary_table(path: Path = LOG) -> str:
    """개선 기록의 "발표용 요약" 절(마크다운). 절이 없으면 빈 문자열."""
    if not path.is_file():
        return ""
    lines, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == SUMMARY_HEADING
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()
