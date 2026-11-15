"""실제 공고(data/raw/*.posting.json)로 추출을 한 바퀴 돌려 보는 확인용 스크립트.

실행 (저장소 루트에서):
  가짜 모드:  $env:BEDROCK_MOCK="1"; .venv\\Scripts\\python -m ai.eval.run_raw
  실제 모드:  $env:BEDROCK_MOCK="0"; $env:BEDROCK_MODEL_ID="..."; $env:AWS_PROFILE="kosa"
              .venv\\Scripts\\python -m ai.eval.run_raw            (공고 1건당 Bedrock 1번, 재시도 시 1번 더)

- 공고마다 ai.extract 로 추출(② + section)하고 pipeline.standardize(#13)로 표준화한다.
- 결과는 outputs/eval/ 에 저장한다. 공고 본문은 저장하지 않지만 ②의 evidence 에 공고 문장이 들어 있다.
  data/raw/ 와 outputs/ 는 .gitignore 대상이다. 결과 파일을 커밋하지 않는다.
- 화면에는 공고 본문을 찍지 않고 표만 보여준다.
- 기술 빈도는 확인용이다. 실제 수요 집계는 backend(#12)가 한다.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
except ImportError:
    pass

from ai.bedrock import BedrockJSONError, add_usage, is_mock  # noqa: E402
from ai.extract import DEFAULT_PROMPT, PROMPTS, extract_with_details  # noqa: E402
from ai.sections import OTHER, SECTION_WEIGHT, _find, headings, weight  # noqa: E402
from pipeline.aliases import normalize_skill  # noqa: E402
from pipeline.standardize import standardize  # noqa: E402

OUT_DIR = ROOT / "outputs" / "eval"
SECTION_ORDER = ("qualification", "duty", "preference", OTHER, None)
SECTION_SHORT = {"qualification": "q", "duty": "d", "preference": "p", OTHER: "other", None: "null"}

# LLM 출력 품질 의심 표시 (사람이 확인할 후보를 고르는 용도. 정답이 아니다)
_BUNDLE = re.compile(r",|·|\s등\b|\s및\s")
_CATEGORY = re.compile(r"도구|API|플랫폼|프레임워크|언어|환경|데이터베이스|기술$|^AI/|/LLM")


def llm_quality(llm_skills: list[dict]) -> dict:
    """LLM 이 낸 이름(코드로 표준화하기 전) 기준: 여러 기술 묶음, 범주, 대표명과 대소문자·표기만 다른 것."""
    bundle, category, case = [], [], []
    for s in llm_skills:
        name = s.get("normalized", "").strip()
        canon = normalize_skill(s.get("raw", "")) or normalize_skill(name)
        if _BUNDLE.search(name):
            bundle.append(name)
        elif canon is None and _CATEGORY.search(name):
            category.append(name)
        if canon and canon != name:
            case.append(f"{name}→{canon}")
    return {"bundle": bundle, "category": category, "not_canonical": case}


def subheading_parent(text: str, evidence: str) -> tuple[str, str] | None:
    """근거 문장이 '모르는 칸 제목' 아래에 있고, 그보다 앞에 아는 칸(자격요건 등)이 있으면 (소제목, 앞의 아는 칸)."""
    i = _find(text, evidence)
    if i < 0:
        return None
    before = [(name, value) for start, name, value in headings(text) if start <= i]
    if not before or before[-1][1] != OTHER:
        return None
    known = [value for _, value in before if value in SECTION_WEIGHT]
    return (before[-1][0], known[-1]) if known else None


def run_posting(p: dict, prompt_version: str = DEFAULT_PROMPT) -> dict:
    hs = headings(p["text"])
    row = {
        "id": p["id"], "title": p["title"], "url": p.get("url"),
        "headings": [(name, value) for _, name, value in hs],
        "attempts": 0, "usage": {}, "error": None,
        "extraction": {"posting_id": p["id"], "skills": []}, "unknown": [], "dropped": [], "subheading_other": [],
        "llm_quality": {"bundle": [], "category": [], "not_canonical": []},
    }
    try:
        extraction, result, dropped = extract_with_details(p, prompt_version)
    except BedrockJSONError as e:
        row["error"], row["usage"] = str(e), e.usage
        return row
    std, unknown = standardize(extraction)
    row.update(
        attempts=result.attempts, usage=result.usage, extraction=std, unknown=unknown,
        dropped=[(s["normalized"], s["evidence"]) for s in dropped],
        llm_quality=llm_quality(result.data["skills"]),
    )
    for s in std["skills"]:
        if s["section"] == OTHER:
            parent = subheading_parent(p["text"], s["evidence"])
            if parent:
                row["subheading_other"].append({"skill": s["normalized"], "subheading": parent[0], "under": parent[1]})
    return row


def print_table(rows: list[dict]) -> None:
    print("\n| id | 제목 | 칸 제목 아는/모르는 | 기술 | section q/d/p/other/null | uncertain | 사전 밖 | 제외 칸 버림 | 토큰 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        skills = r["extraction"]["skills"]
        known = sum(v != OTHER for _, v in r["headings"])
        dist = Counter(s["section"] for s in skills)
        dist_s = "/".join(str(dist.get(k, 0)) for k in SECTION_ORDER)
        unc = sum(s["confidence"] == "uncertain" for s in skills)
        cand = ", ".join(r["unknown"]) or "-"
        tok = r["usage"].get("totalTokens", 0)
        err = " (오류)" if r["error"] else ""
        print(f"| {r['id']} | {r['title'][:25]} | {known}/{len(r['headings']) - known} | {len(skills)}{err} | {dist_s}"
              f" | {unc} | {cand} | {len(r['dropped'])} | {tok} |")


def summarize(rows: list[dict]) -> dict:
    skills = [s for r in rows for s in r["extraction"]["skills"]]
    dist = Counter(s["section"] for s in skills)
    total_usage: dict = {}
    for r in rows:
        add_usage(total_usage, r["usage"])
    unknown_headings = Counter(name for r in rows for name, v in r["headings"] if v == OTHER)
    sub = [x for r in rows for x in r["subheading_other"]]

    freq: dict[str, dict] = defaultdict(lambda: {"postings": 0, "required": 0, "preferred": 0, "weighted": 0.0})
    for r in rows:
        for s in r["extraction"]["skills"]:  # ② 는 공고 안에서 같은 기술을 하나로 합쳐 둔다
            f = freq[s["normalized"]]
            f["postings"] += 1
            f[s["type"]] += 1
            f["weighted"] = round(f["weighted"] + weight(s["type"], s["section"]), 2)
    quality = {k: [x for r in rows for x in r["llm_quality"][k]] for k in ("bundle", "category", "not_canonical")}
    return {
        "postings": len(rows), "errors": sum(bool(r["error"]) for r in rows),
        "skills": len(skills),
        "in_dictionary": sum(normalize_skill(s["normalized"]) is not None for s in skills),
        "llm_quality": quality,
        "section": {SECTION_SHORT[k]: dist.get(k, 0) for k in SECTION_ORDER},
        "uncertain": sum(s["confidence"] == "uncertain" for s in skills),
        "dropped": sum(len(r["dropped"]) for r in rows),
        "usage": total_usage,
        "unknown_headings": unknown_headings.most_common(),
        "subheading_other": sub,
        "dictionary_candidates": Counter(u for r in rows for u in r["unknown"]).most_common(),
        "frequency": sorted(({"skill": k, **v} for k, v in freq.items()), key=lambda x: (-x["postings"], x["skill"])),
    }


def print_summary(s: dict) -> None:
    n = s["skills"] or 1
    sec = s["section"]
    print(f"\n공고 {s['postings']}건 / 오류 {s['errors']}건 / 기술 {s['skills']}개 / uncertain {s['uncertain']} / 제외 칸 버림 {s['dropped']}")
    print(f"사전 안 기술 {s['in_dictionary']}개 ({s['in_dictionary'] / n:.0%})")
    q = s["llm_quality"]
    print(f"LLM 출력 의심: 묶음 {len(q['bundle'])} {q['bundle']} / 범주 {len(q['category'])} {q['category']}"
          f" / 대표명과 표기만 다름 {len(q['not_canonical'])} {q['not_canonical']}")
    print(f"section: q {sec['q']} · d {sec['d']} · p {sec['p']} · other {sec['other']} ({sec['other'] / n:.0%})"
          f" · null {sec['null']} ({sec['null'] / n:.0%})")
    u = s["usage"]
    print(f"토큰 합계 {u.get('totalTokens', 0)} (입력 {u.get('inputTokens', 0)} / 출력 {u.get('outputTokens', 0)})")
    print(f"\n소제목 때문에 other 가 된 기술: {len(s['subheading_other'])}개")
    by_sub = defaultdict(list)
    for x in s["subheading_other"]:
        by_sub[(x["subheading"], x["under"])].append(x["skill"])
    for (sub, under), sk in sorted(by_sub.items()):
        print(f"  [{sub}] (앞 칸 {under}): {', '.join(sk)}")
    print("\n표에 없는 칸 제목 (횟수):")
    for name, c in s["unknown_headings"]:
        print(f"  [{name}] {c}")
    print("\n사전에 없는 기술 후보 (standardize unknown):", ", ".join(f"{k}({c})" for k, c in s["dictionary_candidates"]) or "-")
    print("\n기술 빈도 (확인용, 실제 수요 집계는 #12)")
    print("| 기술 | 공고 수 | required | preferred | 가중 합 |")
    print("| --- | --- | --- | --- | --- |")
    for f in s["frequency"]:
        print(f"| {f['skill']} | {f['postings']} | {f['required']} | {f['preferred']} | {f['weighted']} |")


def main() -> int:
    ap = argparse.ArgumentParser(description="실제 공고로 추출 한 바퀴")
    ap.add_argument("--glob", default=str(ROOT / "data" / "raw" / "*.posting.json"))
    ap.add_argument("--limit", type=int, default=0, help="앞에서부터 N건만 (0 이면 전부)")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default=DEFAULT_PROMPT, help="프롬프트 버전 (기본 v1)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    files = sorted(glob.glob(args.glob))  # 기본값은 data/raw 바로 아래만 본다(하위 폴더의 채점용 공고는 읽지 않음)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"공고 파일이 없습니다: {args.glob}")
        return 1
    prompt = PROMPTS[args.prompt]
    version = prompt.VERSION
    mode = "mock" if is_mock() else "bedrock"
    print(f"프롬프트 {version} / 모드 {mode} / 공고 {len(files)}건")
    if mode == "mock":
        print("가짜 모드: 실제 공고 id 에 맞는 가짜 응답이 없어 모두 빈 결과입니다. 읽기·저장·표 출력만 확인합니다.")

    rows = [run_posting(json.loads(Path(f).read_text(encoding="utf-8")), args.prompt) for f in files]
    summary = summarize(rows)
    print_table(rows)
    print_summary(summary)
    for r in rows:
        if r["error"]:
            print(f"  ! {r['id']}: {r['error']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"raw_run_{version}_{mode}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    meta = {"prompt": version, "mode": mode, "alias_sha256": getattr(prompt, "ALIAS_SHA256", None)}
    out.write_text(json.dumps({**meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n결과 저장 (커밋하지 않음): {out.relative_to(ROOT)}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
