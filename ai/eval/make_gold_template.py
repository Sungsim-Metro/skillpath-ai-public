"""#21 채점용 공고의 빈 정답지 양식 만들기와, 작성한 정답지 검사. 공고 본문과 근거 문장은 화면에 찍지 않는다.

파일 (공고와 정답지는 data/raw/ 아래라 커밋되지 않는다)
  data/raw/eval_test/postings/*.posting.json   채점용 공고 ① (종훈 제공, 새 to_posting 칸 제목 형식)
  data/raw/eval_test/gold_test.json            정답지 ② 배열 (성진이 직접 작성)
  ai/eval/test_manifest.json                   커밋한다. 공고 id 와 본문 sha256 만 (원문·URL·제목 없음)

사용 (저장소 루트에서)
  python -m ai.eval.make_gold_template init    빈 양식과 확인표를 만든다. 정답지나 확인표가 이미 있으면 덮어쓰지 않고 멈춘다.
  python -m ai.eval.make_gold_template check   작성한 정답지를 검사한다. --fill 을 주면 section·confidence·ncs_unit 을 코드로 채운다.

정답지는 기술마다 raw, normalized, type, evidence 만 쓰면 된다(ai/eval/GOLD_RULES.md 규칙).
section 은 근거 문장이 들어 있는 칸을 코드(ai.sections.find_section)로 채우고, confidence 는 high, ncs_unit 은 null 로 둔다.

새는 것을 막는 확인
- 개선용 데이터(샘플 공고, 보관된 로켓펀치 튜닝 공고 12건, dev12_sections/)와 같은 id 나
  같은 본문이 있으면 init 이 멈춘다. 고칠 때 본 공고로 채점하면 과대평가가 된다.
- check 는 공고 본문이 init 때와 같은지(sha256) 먼저 본다. 바뀌었으면 멈춘다.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from ai.evidence import evidence_in_text
from ai.sections import EXCLUDED, find_section
from pipeline.aliases import normalize_skill
from pipeline.reviewed_postings import RAW_DIR, SNAPSHOT

ROOT = Path(__file__).resolve().parents[2]
POSTINGS_DIR = ROOT / "data" / "raw" / "eval_test" / "postings"
GOLD = ROOT / "data" / "raw" / "eval_test" / "gold_test.json"
MANIFEST = ROOT / "ai" / "eval" / "test_manifest.json"
DEV_SOURCES = [
    ROOT / "data" / "sample" / "postings.json",
    *RAW_DIR.glob("*.posting.json"),
    *(RAW_DIR / SNAPSHOT / "legacy-postings").glob("*.posting.json"),
    *(RAW_DIR / "dev12_sections").glob("*.posting.json"),
]
TYPES = ("required", "preferred")
GOLD_KEYS = ("raw", "normalized", "type", "section", "evidence", "confidence", "ncs_unit")


def shown(path: Path) -> str:
    """저장소 안이면 상대 경로, 밖이면(시험용 폴더 등) 전체 경로."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256(text: str) -> str:
    """본문 지문. 16진수 대신 base32(소문자, a-z2-7)로 적는다. 16진수 해시에는 우연히 12자리 숫자나 전화번호
    모양이 생겨 공개 동기화 검사(.github/public-sync/build.sh)가 막히기 때문이다(0·1이 없고 숫자 연속이 거의 없다)."""
    return base64.b32encode(hashlib.sha256(text.encode("utf-8")).digest()).decode().lower().rstrip("=")


def load_postings(folder: Path) -> list[dict]:
    files = sorted(folder.glob("*.posting.json"))
    postings = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    for f, p in zip(files, postings):
        if not isinstance(p.get("id"), str) or not isinstance(p.get("text"), str) or not p["text"].strip():
            raise SystemExit(f"공고 형식 오류: {f.name} 에 id 나 text 가 없습니다")
    ids = [p["id"] for p in postings]
    if len(set(ids)) != len(ids):
        raise SystemExit("같은 id 의 공고가 두 개 이상 있습니다")
    return postings


def dev_postings(sources: list[Path]) -> list[dict]:
    out = []
    for path in sources:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        out += data if isinstance(data, list) else [data]
    return out


def init(postings_dir: Path, gold: Path, manifest: Path, dev_sources: list[Path]) -> int:
    postings = load_postings(postings_dir)
    if not postings:
        print(f"채점용 공고가 없습니다: {postings_dir}")
        return 1
    dev = dev_postings(dev_sources)
    dev_ids = {p.get("id") or p.get("posting_id") for p in dev}
    dev_hashes = {sha256(p["text"]) for p in dev if p.get("text")}
    leaked = [p["id"] for p in postings if p["id"] in dev_ids or sha256(p["text"]) in dev_hashes]
    if leaked:
        print(f"개선용 데이터와 겹치는 공고가 있어 멈춥니다(id 또는 본문이 같음): {leaked}")
        return 1
    if gold.exists():
        print(f"정답지가 이미 있어 덮어쓰지 않습니다: {shown(gold)}")
        return 1
    if manifest.exists():
        # 정답지만 지워진 경우에도 확인표(채점 때 본문 대조 기준)를 새 공고로 바꿔치지 않게 한다
        print(f"확인표가 이미 있어 덮어쓰지 않습니다: {shown(manifest)}. 다시 만들려면 이유를 남기고 직접 지우세요.")
        return 1
    gold.parent.mkdir(parents=True, exist_ok=True)
    gold.write_text(
        json.dumps([{"posting_id": p["id"], "skills": []} for p in postings], ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    manifest.write_text(json.dumps({
        "note": "#21 채점용 공고 확인표. 본문 원문·URL·제목은 두지 않는다. 공고와 정답지는 data/raw/eval_test/ (커밋 안 함)",
        "created": date.today().isoformat(),
        "count": len(postings),
        "postings": [{"id": p["id"], "text_sha256": sha256(p["text"])} for p in postings],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"채점용 공고 {len(postings)}건, 개선용 데이터와 겹침 없음")
    print(f"빈 정답지: {shown(gold)} (커밋하지 않음)")
    print(f"확인표: {shown(manifest)} (id 와 sha256 만, 커밋함)")
    print("id:", ", ".join(p["id"] for p in postings))
    return 0


def check(postings_dir: Path, gold: Path, manifest: Path, fill: bool) -> int:
    if not manifest.exists():
        print(f"확인표가 없습니다: {shown(manifest)}. 먼저 init 을 하세요.")
        return 1
    postings = {p["id"]: p for p in load_postings(postings_dir)}
    expected = {x["id"]: x["text_sha256"] for x in json.loads(manifest.read_text(encoding="utf-8"))["postings"]}
    changed = [pid for pid, h in expected.items() if pid not in postings or sha256(postings[pid]["text"]) != h]
    extra = sorted(set(postings) - set(expected))
    if changed or extra:
        print(f"공고가 init 때와 다릅니다. 바뀌었거나 없음: {changed} / 확인표에 없음: {extra}")
        return 1
    data = json.loads(gold.read_text(encoding="utf-8"))
    errors, outside, n_skills, empty = [], 0, 0, 0
    gold_ids = [g.get("posting_id") for g in data]
    if sorted(gold_ids) != sorted(expected):
        errors.append(f"정답지의 공고 id 가 확인표와 다릅니다: 빠짐 {sorted(set(expected) - set(gold_ids))}, "
                      f"더해짐 {sorted(set(gold_ids) - set(expected))}")
    for g in data:
        pid, text = g.get("posting_id"), postings.get(g.get("posting_id"), {}).get("text", "")
        skills = g.get("skills", [])
        empty += not skills
        names = [s.get("normalized") for s in skills]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            errors.append(f"{pid}: 같은 기술이 두 번 있습니다 ({dup})")
        for i, s in enumerate(skills):
            n_skills += 1
            where = f"{pid} skills[{i}] {s.get('normalized')!r}"
            missing = [k for k in ("raw", "normalized", "type", "evidence") if not isinstance(s.get(k), str) or not s[k].strip()]
            if missing:
                errors.append(f"{where}: 비었거나 없는 칸 {missing}")
                continue
            if s["type"] not in TYPES:
                errors.append(f"{where}: type 은 required 또는 preferred")
            if not evidence_in_text(s["evidence"], text):
                errors.append(f"{where}: 근거 문장이 공고 본문에 그대로 없습니다")
            canon = normalize_skill(s["raw"])
            if canon is None:
                outside += 1  # 사전 밖 기술(정답지 규칙 7번). 오류가 아니다
            elif canon != s["normalized"]:
                errors.append(f"{where}: 사전에서 raw 의 대표명은 {canon!r} 입니다")
            section = find_section(text, s["evidence"])
            if section == EXCLUDED:
                errors.append(f"{where}: 근거 문장이 제외 칸(혜택·회사소개 등)에 있습니다")
            if fill:
                s.update(section=section, confidence=s.get("confidence") or "high", ncs_unit=s.get("ncs_unit"))
                g["skills"][i] = {k: s.get(k) for k in GOLD_KEYS}
    print(f"정답지 공고 {len(data)}건, 기술 {n_skills}개 (빈 공고 {empty}건, 사전 밖 기술 {outside}개)")
    for e in errors:
        print("  ! " + e)
    if errors:
        print(f"오류 {len(errors)}개. 고친 뒤 다시 check 하세요." + (" (--fill 은 오류가 없을 때만 저장)" if fill else ""))
        return 1
    if fill:
        gold.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("section·confidence·ncs_unit 을 채워 저장했습니다.")
    print("오류 없음")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="#21 채점용 정답지 양식과 검사")
    ap.add_argument("command", choices=("init", "check"))
    ap.add_argument("--fill", action="store_true", help="check 때 section·confidence·ncs_unit 을 코드로 채워 저장")
    ap.add_argument("--postings-dir", type=Path, default=POSTINGS_DIR)
    ap.add_argument("--gold", type=Path, default=GOLD)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args()
    if args.command == "init":
        return init(args.postings_dir, args.gold, args.manifest, DEV_SOURCES)
    return check(args.postings_dir, args.gold, args.manifest, args.fill)


if __name__ == "__main__":
    sys.exit(main())
