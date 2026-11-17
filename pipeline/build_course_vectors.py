"""교육과정 청크를 임베딩해 data/courses/course_vectors.json 으로 저장한다 (#14).

실행 (저장소 루트에서, AWS 로그인 필요: aws sso login --profile kosa):
  python -X utf8 -m pipeline.build_course_vectors --dry-run   # 건수와 글자 수만 본다(호출 없음)
  BEDROCK_MOCK=0 python -X utf8 -m pipeline.build_course_vectors

- 모델은 .env 의 EMBED_MODEL_ID. 파일에 모델 ID 를 적어 두고, 서비스는 현재 모델과 다르면 이 파일을 쓰지 않는다.
- 같은 문장은 data/raw/embed_cache.json 에 저장돼 다시 부르지 않는다.
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="건수와 글자 수만 출력하고 호출하지 않는다")
    args = ap.parse_args()

    chunks = all_chunks()
    chars = sum(len(c["text"]) for c in chunks)
    print(f"청크 {len(chunks)}개, 글자 {chars:,}자 (과정 {len({c['course_id'] for c in chunks})}개)")
    if args.dry_run:
        return
    if embed.is_mock():
        raise SystemExit("가짜 모드(BEDROCK_MOCK=1)에서는 만들지 않습니다. BEDROCK_MOCK=0 으로 실행하세요.")

    vectors = embed.embed_many([c["text"] for c in chunks])
    items = [{"course_id": c["course_id"], "field": c["field"], "vector": [round(x, 3) for x in v]}
             for c, v in zip(chunks, vectors)]
    OUT.write_text(json.dumps({"model": os.environ["EMBED_MODEL_ID"], "dim": len(items[0]["vector"]), "items": items},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} ({OUT.stat().st_size / 1e6:.1f}MB) / 실제 호출 {embed.usage()}")


if __name__ == "__main__":
    main()
