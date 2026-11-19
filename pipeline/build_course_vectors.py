"""교육과정 청크를 임베딩해 data/courses/course_vectors.json 으로 저장한다 (#14).

실행 (저장소 루트에서, AWS 로그인 필요: aws sso login --profile kosa):
  python -X utf8 -m pipeline.build_course_vectors --dry-run   # 건수와 글자 수만 본다(호출 없음)
  BEDROCK_MOCK=0 python -X utf8 -m pipeline.build_course_vectors

- 모델은 .env 의 EMBED_MODEL_ID. 파일에 모델 ID 를 적어 두고, 서비스는 현재 모델과 다르면 이 파일을 쓰지 않는다.
- 같은 문장은 data/raw/embed_cache.json 에 저장돼 다시 부르지 않는다.
- 기존 course_vectors.json 이 같은 모델로 만든 것이면 이미 있는 (과정, 필드) 벡터는 다시 쓰고 새 청크만 부른다
  (2026-11-19: 캐시가 없는 PC에서 AI 과정만 더할 때). 처음부터 다시 만들려면 --full.
- 가짜 모드(BEDROCK_MOCK=1)의 벡터는 점수에 의미가 없어 만들지 않는다.
- 소수점 3자리로 줄여 저장한다(파일 크기). 코사인 순위에는 영향이 거의 없다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pipeline import embed
from pipeline.course_chunks import all_chunks

OUT = Path(__file__).resolve().parent.parent / "data" / "courses" / "course_vectors.json"


def existing_vectors(model: str) -> dict[tuple[str, str], list[float]]:
    """기존 파일의 (과정 id, 필드) → 벡터. 파일이 없거나 다른 모델로 만들었으면 빈 표.

    ponytail: 과정 본문이 바뀌어도 (과정, 필드)가 같으면 예전 벡터를 쓴다. 과정 표를 다시 수집했으면 --full 로 돌린다.
    """
    if not OUT.exists():
        return {}
    data = json.loads(OUT.read_text(encoding="utf-8"))
    if data.get("model") != model:
        return {}
    return {(i["course_id"], i["field"]): i["vector"] for i in data["items"]}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="건수와 글자 수만 출력하고 호출하지 않는다")
    ap.add_argument("--full", action="store_true", help="기존 벡터를 쓰지 않고 전부 다시 임베딩한다")
    args = ap.parse_args()

    chunks = all_chunks()
    chars = sum(len(c["text"]) for c in chunks)
    print(f"청크 {len(chunks)}개, 글자 {chars:,}자 (과정 {len({c['course_id'] for c in chunks})}개)")
    embed.is_mock()  # .env 를 읽어 EMBED_MODEL_ID 를 환경 변수로 올린다
    reuse = {} if args.full else existing_vectors(os.getenv("EMBED_MODEL_ID", ""))
    todo = [c for c in chunks if (c["course_id"], c["field"]) not in reuse]
    print(f"기존 벡터 재사용 {len(chunks) - len(todo)}개, 새로 부를 청크 {len(todo)}개")
    if args.dry_run:
        return
    if embed.is_mock():
        raise SystemExit("가짜 모드(BEDROCK_MOCK=1)에서는 만들지 않습니다. BEDROCK_MOCK=0 으로 실행하세요.")

    new = dict(zip(((c["course_id"], c["field"]) for c in todo), embed.embed_many([c["text"] for c in todo])))
    items = [{"course_id": c["course_id"], "field": c["field"],
              "vector": reuse.get((c["course_id"], c["field"])) or [round(x, 3) for x in new[(c["course_id"], c["field"])]]}
             for c in chunks]
    OUT.write_text(json.dumps({"model": os.environ["EMBED_MODEL_ID"], "dim": len(items[0]["vector"]), "items": items},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} ({OUT.stat().st_size / 1e6:.1f}MB) / 실제 호출 {embed.usage()}")


if __name__ == "__main__":
    main()
