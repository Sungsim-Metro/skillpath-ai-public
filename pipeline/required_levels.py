"""공고가 요구하는 기술별 기준 단계를 정한다 (#90, 2026-11-19).

공고 추출 결과(②)의 근거 문장을 그 기술의 세부 능력 단계 예시(data/skill_levels.json) 5개와 임베딩으로 비교해
가장 비슷한 단계를 "그 공고가 요구한 단계"로 본다. 기술별 기준 단계는 공고별 요구 단계의 중앙값이다(코드로 계산).

- 수요 집계와 같이 confidence 가 "high" 인 항목만 쓴다(근거 문장이 공고에 있는 것, 원칙 3).
- 가장 높은 유사도가 MIN_SIMILARITY 보다 낮은 문장은 버린다. 쓸 수 있는 공고가 MIN_POSTINGS 건 미만이면
  기본 단계(backend.levels.DEFAULT_REQUIRED_LEVEL)와 "uncertain" 으로 둔다(원칙 4).
- 공통 기술("ALL", AWS 등)은 비교할 단계 예시가 없어 기본 단계와 "uncertain" 이다.
- 결과 파일에는 집계 숫자만 쓴다. 공고 문장은 저장하지 않는다(공개 저장소).

실행:
  BEDROCK_MOCK=1 python -m pipeline.required_levels     # data/sample 의 추출 결과, 가짜 벡터(점수에 의미 없음)
  BEDROCK_MOCK=0 python -m pipeline.required_levels     # DynamoDB 추출 결과, Titan 임베딩(EMBED_MODEL_ID)
  BEDROCK_MOCK=0 python -m pipeline.required_levels --job ai_engineer   # AI 엔지니어 → data/required_levels_ai_engineer.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from statistics import median_low

from backend.jobs import DEFAULT_JOB, job_file
from backend.levels import ALL, DATA_DIR, DEFAULT_REQUIRED_LEVEL, job_subskills, subskills_for
from pipeline import embed

MIN_SIMILARITY = 0.3  # 팀 결정값(근거 자료 없음). 이보다 덜 비슷한 문장은 단계를 가르지 못한다고 본다
MIN_POSTINGS = 3  # 기준 단계를 정하려면 필요한 공고 수
OUT_FILE = DATA_DIR / "required_levels.json"  # 클라우드. 다른 직무는 backend.jobs.job_file 이름(예: required_levels_ai_engineer.json)
SAMPLE_FILES = ("extraction.json", "extraction_deduplicated.json")


def load_extractions(job_category: str) -> tuple[list[dict], str]:
    """(추출 결과 목록, 출처 이름). 가짜 모드는 data/sample, 실제 모드는 DynamoDB."""
    if embed.is_mock():
        out = [json.loads((DATA_DIR / "sample" / name).read_text(encoding="utf-8")) for name in SAMPLE_FILES]
        return out, "sample"
    from backend.dynamodb import DynamoDBStore, json_numbers

    store = DynamoDBStore.from_env()
    items = store.snapshot_extractions(job_category, store.get_demand_snapshot(job_category))
    return [json_numbers(item["extraction"]) for item in items if "extraction" in item], "dynamodb"


def _examples(job_category: str = DEFAULT_JOB) -> tuple[list[tuple[str, int]], list[str]]:
    """((세부 능력 id, 단계) 목록, 그 순서의 예시 문장 목록). 직무의 진단표만 쓴다."""
    keys, texts = [], []
    for sid, row in job_subskills(job_category).items():
        for level, text in row["levels"].items():
            keys.append((sid, int(level)))
            texts.append(text)
    return keys, texts


def posting_levels(extractions: list[dict], job_category: str = DEFAULT_JOB) -> dict[str, list[int]]:
    """{기술: [공고별 요구 단계, ...]}. 한 공고에서 같은 기술이 여러 번 나오면 가장 높은 단계 하나만 센다."""
    keys, texts = _examples(job_category)
    vectors = dict(zip(keys, embed.embed_many(texts)))
    found: dict[str, list[int]] = {}
    for extraction in extractions:
        per_posting: dict[str, int] = {}
        for item in extraction.get("skills", []):
            skill, evidence = item.get("normalized"), (item.get("evidence") or "").strip()
            if not skill or not evidence or item.get("confidence") != "high":
                continue
            linked = subskills_for(skill, job_category)
            if isinstance(linked, list):  # 다른 직무 진단표의 세부 능력(클라우드 C* 등)은 이 직무의 예시가 없어 뺀다
                linked = [sid for sid in linked if (sid, 1) in vectors]
            if linked == ALL or not linked:
                continue
            sentence = embed.embed(evidence)
            score, level = max((embed.cosine(sentence, vectors[(sid, lv)]), lv)
                               for sid in linked for lv in range(1, 6))
            if score < MIN_SIMILARITY:
                continue
            per_posting[skill] = max(level, per_posting.get(skill, 0))
        for skill, level in per_posting.items():
            found.setdefault(skill, []).append(level)
    return found


def summarize(found: dict[str, list[int]]) -> dict[str, dict]:
    """기술별 {required_level, confidence, postings_used, distribution}. 계산은 코드로 한다."""
    out = {}
    for skill in sorted(found):
        levels = found[skill]
        enough = len(levels) >= MIN_POSTINGS
        out[skill] = {
            "required_level": median_low(levels) if enough else DEFAULT_REQUIRED_LEVEL,
            "confidence": "certain" if enough else "uncertain",
            "postings_used": len(levels),
            "distribution": {str(lv): levels.count(lv) for lv in range(1, 6) if lv in levels},
        }
    return out


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", default=DEFAULT_JOB)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    extractions, source = load_extractions(args.job)
    skills = summarize(posting_levels(extractions, args.job))
    out = args.out or DATA_DIR / job_file("required_levels.json", args.job)
    payload = {
        "note": "기술별 기준 단계. pipeline/required_levels.py 가 공고 근거 문장과 단계 예시(data/skill_levels.json)를 "
                "임베딩으로 비교해 만든다. 공고 문장은 저장하지 않는다. 파일에 없는 기술은 기본 단계와 uncertain 이다.",
        "job_category": args.job,
        "source": source,
        "generated": date.today().isoformat(),
        "min_similarity": MIN_SIMILARITY,
        "min_postings": MIN_POSTINGS,
        "default_level": DEFAULT_REQUIRED_LEVEL,
        "postings": len(extractions),
        "skills": skills,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    usage = embed.usage()
    print(f"공고 {len(extractions)}건({source}), 기준 단계를 정한 기술 {sum(r['confidence'] == 'certain' for r in skills.values())}개"
          f" / 비교한 기술 {len(skills)}개. 임베딩 호출 {usage['calls']}회, 토큰 {usage['tokens']}")
    return out


if __name__ == "__main__":
    os.environ.setdefault("BEDROCK_MOCK", "1")
    main()
