"""저장된 추출 결과를 지금의 별칭 사전으로 다시 표준화하고, 수요 집계를 다시 만든다 (LLM 호출 없음).

주의(2026-11-20 확인): 지금 저장된 추출 결과는 전부 prompt v1(extract_v1)이다. v1 은 대표명 12개 안의 기술만 high 로 센다
(코드의 DEFAULT_PROMPT 도 "v1"). 그래서 수요 집계에 기술이 12개뿐이고, 사전을 늘려도 v1 결과에는 반영되지 않는다.
이 도구는 저장된 v1 LLM 출력(raw·evidence)에 **v2.1 의 사전 기반 후처리**를 다시 적용한다. 진짜 v2.1 재추출과 같지 않다
(v2.1 프롬프트의 추출 규칙은 적용되지 않는다). 그대로 쓸지, v2.1 로 재추출할지는 팀이 정한다.

배경: 사전을 보강해도 DynamoDB 의 추출 결과와 수요 집계는 옛 기준으로 남는다(입력 지문에 사전이 없어 배치를 다시 돌려도
옛 결과를 재사용한다). 추출 단계의 후처리(ai.extract.to_contract_with_dropped + standardize)를 저장된
raw·normalized·evidence 에 그대로 다시 적용해, 기술 이름·confidence 를 코드로 다시 정한다.

- 기본은 **드라이런**이다. DynamoDB 는 읽기만 하고, 바뀌는 건수와 집계 변화를 보여 준다.
- `--apply` 를 줘야 쓴다. 쓰기 전에 현재 추출 결과와 수요 집계를 `--backup-dir` 에 JSON 으로 저장한다.
- 고정한 입력(input_fingerprint)과 선정 결과는 그대로 둔다. 바뀌는 것은 추출 결과의 기술 이름·confidence·ncs_unit 뿐이다.
- 공고 본문은 S3(posting_s3_key)에서 읽는다. 근거 문장이 본문에 있는지 코드로 다시 검사하기 위해서다.

실행(SSO 로그인 후):
  BEDROCK_MOCK=0 python -m pipeline.restandardize --job-category ai_engineer          # 드라이런
  BEDROCK_MOCK=0 python -m pipeline.restandardize --job-category ai_engineer --apply --backup-dir outputs/backup

복구(--apply 가 중간에 실패했거나 결과가 마음에 안 들 때): 백업 파일 이름의 시각(예: 20261121T010203Z)을 준다.
  BEDROCK_MOCK=0 python -m pipeline.restandardize --job-category ai_engineer --backup-dir outputs/backup --restore 20261121T010203Z          # 확인만
  BEDROCK_MOCK=0 python -m pipeline.restandardize --job-category ai_engineer --backup-dir outputs/backup --restore 20261121T010203Z --apply  # 되돌려 씀
쓰는 순서는 추출 결과 → 수요 집계(마지막)다. 중간에 실패하면 둘이 어긋나므로 같은 명령을 다시 돌리거나(같은 건을 다시 쓰므로 안전) --restore 로 되돌린다.
쓰기 전에 모든 항목이 DynamoDB 형식(float 금지)으로 바뀌는지 먼저 검사해, 도중에 형식 오류로 멈추는 일을 막는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ai.extract import PROMPTS, to_contract_with_dropped
from backend.demand import build_snapshot
from backend.dynamodb import DynamoDBStore, json_numbers
from backend.jobs import ANALYSIS_JOBS
from pipeline.standardize import standardize

TOP = 15


def read_text(s3, bucket: str, key: str) -> str:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        return json.loads(body.read().decode("utf-8-sig"))["text"]
    finally:
        body.close()


def restandardize(extraction: dict, text: str, prompt_version: str) -> dict:
    """추출 단계의 후처리를 저장된 값에 다시 적용한다."""
    llm_like = [{"raw": s["raw"], "normalized": s["normalized"], "type": s["type"], "evidence": s["evidence"]}
                for s in extraction["skills"]]
    skills, _ = to_contract_with_dropped(llm_like, text, prompt_version)
    new, _ = standardize({"posting_id": extraction["posting_id"], "skills": skills})
    return new


def _counts(extraction: dict) -> Counter:
    return Counter(s["confidence"] for s in extraction["skills"])


def plan(items: list[dict], texts: dict[str, str], prompt_version: str = "v2.1") -> tuple[list[dict], dict]:
    """(바뀐 항목 목록, 요약). 입력은 고치지 않는다."""
    changed, flips, gained, lost = [], Counter(), Counter(), Counter()
    before, after = Counter(), Counter()
    for item in items:
        old = item["extraction"]
        new = restandardize(old, texts[item["posting_id"]], prompt_version)
        before.update(_counts(old)); after.update(_counts(new))
        old_high = {s["normalized"] for s in old["skills"] if s["confidence"] == "high"}
        new_high = {s["normalized"] for s in new["skills"] if s["confidence"] == "high"}
        gained.update(new_high - old_high); lost.update(old_high - new_high)
        if new != old:
            changed.append({**item, "extraction": new})
        flips.update(["changed" if new != old else "same"])
    summary = {"postings": len(items), **flips, "confidence_before": dict(before), "confidence_after": dict(after),
               "gained_high": gained.most_common(TOP), "lost_high": lost.most_common(TOP)}
    return changed, summary


def snapshot_from(items: list[dict], current: dict, run_id: str) -> dict:
    """지금 공개된 집계와 같은 입력·선정 규칙으로 새 스냅샷을 만든다."""
    selected = {row["posting_id"]: row["input_fingerprint"] for row in current["counted_postings"]}
    exclusions = {row["posting_id"]: row["reason"] for row in current["quality"]["excluded_postings"]}
    return build_snapshot(
        items, selected=selected, job_category=current["job_category"], collected=current["demand"]["collected"],
        exclusions=exclusions, selection_version=current["policy"]["selection_version"], run_id=run_id,
    )


def _top(snapshot: dict) -> list[tuple[str, int]]:
    return [(row["skill"], row["postings"]) for row in snapshot["demand"]["skills"][:TOP]]


def check_writable(values: list[dict]) -> None:
    """DynamoDB 가 받을 수 있는 형식인지(float 금지 등) 쓰기 전에 모두 검사한다. 안 되면 TypeError, 아무것도 쓰지 않는다."""
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    for value in values:
        serializer.serialize(value)


def restore(store, backup_dir: Path, job_category: str, stamp: str, apply: bool) -> int:
    """백업 파일(추출 결과 목록, 수요 집계)로 되돌린다. 형식 검사를 먼저 하고 집계를 마지막에 쓴다."""
    extractions = json.loads((backup_dir / f"{job_category}-extractions-{stamp}.json").read_text(encoding="utf-8"))
    demand = json.loads((backup_dir / f"{job_category}-demand-{stamp}.json").read_text(encoding="utf-8"))
    check_writable([*extractions, demand])
    print(f"복구 대상: 추출 결과 {len(extractions)}건과 수요 집계(snapshot {demand.get('snapshot_id')})")
    if not apply:
        print("확인만 했습니다. 아무것도 쓰지 않았습니다(--apply 를 주면 되돌려 씁니다).")
        return 0
    for item in extractions:
        store.save_extraction(item)
    store.save_demand(demand)
    print("복구했습니다.")
    return 0


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-category", required=True, choices=sorted(ANALYSIS_JOBS))
    parser.add_argument("--prompt-version", default="v2.1", choices=sorted(PROMPTS),
                        help="후처리 규칙 버전(기본 v2.1: 사전에 있는 기술이면 high). v1 이면 12개 대표명만 high")
    parser.add_argument("--apply", action="store_true", help="DynamoDB 에 쓴다(기본은 드라이런)")
    parser.add_argument("--backup-dir", type=Path, help="--apply 때 쓰기 전에 현재 값을 저장할 폴더(필수)")
    parser.add_argument("--restore", metavar="STAMP", help="이 시각의 백업으로 되돌린다(--backup-dir 필요, 쓰려면 --apply)")
    args = parser.parse_args()
    if (args.apply or args.restore) and not args.backup_dir:
        parser.error("--apply 와 --restore 는 --backup-dir 가 있어야 한다")
    if os.getenv("BEDROCK_MOCK", "1") == "1":
        parser.exit(1, "BEDROCK_MOCK=0 과 AWS 로그인(SSO)이 필요합니다. 가짜 모드에서는 DynamoDB 를 읽지 않습니다.\n")

    import boto3
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # 이미 설정된 환경 변수는 덮어쓰지 않는다
    os.environ.setdefault("DYNAMODB_EXTRACTIONS_TABLE", "skillpath-posting-extractions")
    os.environ.setdefault("DYNAMODB_DEMAND_TABLE", "skillpath-demand")
    store = DynamoDBStore.from_env()
    if args.restore:
        return restore(store, args.backup_dir, args.job_category, args.restore, args.apply)
    current = store.get_demand_snapshot(args.job_category)
    if current is None:
        parser.exit(1, "공개된 수요 집계가 없어 비교할 수 없습니다.\n")
    current = json_numbers(current)
    items = [json_numbers(i) for i in store.list_extractions(args.job_category)]
    selected = {row["posting_id"] for row in current["counted_postings"]}
    items = [i for i in items if i["posting_id"] in selected]  # 지금 집계에 들어간 공고만 다룬다

    s3 = boto3.client("s3")
    bucket = os.getenv("ROCKETPUNCH_S3_BUCKET", "").strip()
    if not bucket:  # 설정이 없으면 공고 데이터 버킷(이름이 skillpath-ai-data- 로 시작)을 찾는다
        names = [b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith("skillpath-ai-data-")]
        if len(names) != 1:
            parser.exit(1, "공고 버킷을 하나로 정할 수 없습니다. ROCKETPUNCH_S3_BUCKET 을 설정하세요.\n")
        bucket = names[0]
    with ThreadPoolExecutor(16) as pool:
        texts = dict(zip((i["posting_id"] for i in items),
                         pool.map(lambda i: read_text(s3, bucket, i["posting_s3_key"]), items)))

    stored = Counter(i["analysis"].get("prompt_version") for i in items)
    changed, summary = plan(items, texts, args.prompt_version)
    run_id = "restandardize-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    merged = {i["posting_id"]: i for i in items} | {c["posting_id"]: c for c in changed}
    new_snapshot = snapshot_from(list(merged.values()), current, run_id)

    print(f"저장된 추출의 prompt_version: {dict(stored)} → 후처리 규칙 {args.prompt_version} 로 다시 계산")
    print(f"직무 {args.job_category}: 집계 대상 공고 {summary['postings']}건 중 추출 결과가 바뀌는 공고 {len(changed)}건")
    print("confidence 변화:", summary["confidence_before"], "→", summary["confidence_after"])
    print("새로 high 가 되는 기술 상위:", summary["gained_high"])
    print("high 에서 빠지는 기술 상위:", summary["lost_high"] or "없음")
    old_skills, new_skills = current["demand"]["skills"], new_snapshot["demand"]["skills"]
    print(f"수요 기술 수: {len(old_skills)}개 → {len(new_skills)}개 "
          f"(공고 수 {current['demand']['total_postings']} → {new_snapshot['demand']['total_postings']})")
    print("옛 상위:", _top(current))
    print("새 상위:", _top(new_snapshot))

    if not args.apply:
        print("\n드라이런입니다. 아무것도 쓰지 않았습니다.")
        return 0
    marker_check = [{**i, "analysis": {**i["analysis"], "restandardized_at": "x", "restandardized_rules": "x"}} for i in changed]
    check_writable([*marker_check, new_snapshot])  # 형식 오류는 백업·쓰기 전에 여기서 멈춘다
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _dump(args.backup_dir / f"{args.job_category}-demand-{stamp}.json", current)
    _dump(args.backup_dir / f"{args.job_category}-extractions-{stamp}.json", items)
    print(f"백업 저장: {args.backup_dir}")
    marker = {"restandardized_at": stamp, "restandardized_rules": args.prompt_version}
    for item in changed:  # 어떤 규칙으로 다시 계산했는지 남긴다(prompt_version 은 원래 추출 값 그대로)
        store.save_extraction({**item, "analysis": {**item["analysis"], **marker}})
    store.save_demand(new_snapshot)
    print(f"추출 결과 {len(changed)}건과 수요 집계를 저장했습니다(run_id {run_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
