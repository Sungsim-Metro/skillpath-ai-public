"""수집한 교육과정 원문을 전처리해 과정 표와 과정↔세부 능력 연결표를 만든다.

입력:  data/raw/courses/*.md, data/raw/courses/search_hits.csv, pipeline/courses/queries.csv
출력:  data/courses/courses.csv          과정 1개당 1행 (설명, 학습 목표, 소요 시간 등)
       data/courses/course_subskill.csv  과정 ↔ 세부 능력 연결과 근거 문장

연결 규칙 (전부 코드로 판단, LLM 없음):
1. 세부 능력 검색어로 검색했을 때 상위에 나온 과정만 그 세부 능력 후보가 된다.
2. 학습 목표 → 과정 제목 → 설명 순서로, 세부 능력 별칭이 처음 나오는 문장을 근거로 붙인다.
3. 근거 문장이 원문 파일에 그대로 있는지 다시 검사한다.
4. 학습 목표나 제목에서 근거를 찾으면 match=certain.
   설명에서만 찾았거나 근거가 없으면 match=uncertain.
   (설명에는 "Windows or Linux desktops"처럼 지나가며 언급한 단어가 많아서, 그것만으로는 그 능력을 가르친다고 보지 않는다.)
"""

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "courses"
QUERIES_FILE = Path(__file__).with_name("queries.csv")
OUT_DIR = ROOT / "data" / "courses"

SECTION_HEADS = [
    "Description", "Objectives", "AWS Services", "Intended Audience",
    "Recommended previous knowledge", "Ratings", "Course outline", "Outline",
]
LEVEL_KO = {"fundamental": "기초", "intermediate": "중급", "advanced": "고급"}


def split_front_matter(text: str):
    meta, body = {}, text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body.strip()


def sections(body: str) -> dict:
    """상세 페이지 본문을 제목 줄 기준으로 나눈다. 'Details'·'Outline' 탭 이름 줄은 건너뛴다."""
    lines = [l.strip() for l in body.splitlines()]
    out, cur = {"_head": []}, "_head"
    for line in lines:
        if line in SECTION_HEADS and not (line == "Outline" and cur == "_head"):
            cur = line
            out.setdefault(cur, [])
            continue
        if line:
            out.setdefault(cur, []).append(line)
    return out


def parse_duration(head: list[str]):
    for line in head:
        m = re.match(r"Course duration (.+)", line)
        if m:
            text = m.group(1)
            h = re.search(r"(\d+)\s*hour", text)
            mnt = re.search(r"(\d+)\s*minute", text)
            minutes = (int(h.group(1)) * 60 if h else 0) + (int(mnt.group(1)) if mnt else 0)
            return text, minutes or ""
    return "", ""


def parse_language(head: list[str]):
    lang, more = "", 0
    for line in head:
        if line.startswith("Selected language "):
            lang = line.removeprefix("Selected language ").strip()
        m = re.fullmatch(r"\+(\d+) more", line)
        if m:
            more = int(m.group(1))
    return lang, more


def split_sentences(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        for s in re.split(r"(?<=[.!?])\s+", line):
            s = s.strip()
            if len(s) >= 15:
                out.append(s)
    return out


def alias_pattern(aliases: str):
    parts = [re.escape(a.strip()) for a in aliases.split("|") if a.strip()]
    # 앞쪽만 단어 경계를 둔다: 'migrat'가 'migration'에 걸리고, 'log'가 'technology'에 걸리지 않게.
    return re.compile(r"(?<![A-Za-z])(" + "|".join(parts) + ")", re.IGNORECASE)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queries = {q["subskill_id"]: q for q in csv.DictReader(QUERIES_FILE.open(encoding="utf-8"))}
    hits = list(csv.DictReader((RAW_DIR / "search_hits.csv").open(encoding="utf-8")))

    levels_by_course = {}
    for h in hits:
        levels_by_course.setdefault(h["course_id"], set()).add(h["level"])

    courses, parsed = [], {}
    for path in sorted(RAW_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = split_front_matter(raw)
        sec = sections(body)
        head = sec.get("_head", [])
        title = next((l for l in head if not l.startswith("Welcome to AWS Skill Builder")), "")
        duration_text, duration_min = parse_duration(head)
        lang, more = parse_language(head)
        cid = meta.get("course_id", path.stem)
        lv = levels_by_course.get(cid, set())
        level = "fundamental" if "fundamental" in lv else (sorted(lv)[0] if lv else "")
        row = {
            "course_id": cid,
            "title": title,
            "url": meta.get("source_url", ""),
            "level": LEVEL_KO.get(level, ""),
            "retiring": (re.match(r"\[RETIRING - ([0-9-]+)\]", title) or [None, ""])[1],
            "access": "무료",  # 수집 단계에서 accessTier=free 로만 검색했다
            "product_type": "디지털 과정" if any("Digital course" in l for l in head) else "",
            "duration_text": duration_text,
            "duration_min": duration_min,
            "language": lang,
            "other_languages": more,
            "description": " ".join(sec.get("Description", [])),
            "objectives": " / ".join(sec.get("Objectives", [])),
            "aws_services": " / ".join(sec.get("AWS Services", [])),
            "audience": " / ".join(sec.get("Intended Audience", [])),
            "prerequisites": " / ".join(sec.get("Recommended previous knowledge", [])),
            "source": meta.get("source", ""),
            "collected_at": meta.get("collected_at", ""),
        }
        courses.append(row)
        parsed[cid] = {"raw": raw, "title": title, "objectives": sec.get("Objectives", []),
                       "description": sec.get("Description", [])}

    links, seen = [], set()
    for h in hits:
        key = (h["course_id"], h["subskill_id"])
        if key in seen or h["course_id"] not in parsed:
            continue
        seen.add(key)
        q = queries[h["subskill_id"]]
        pat = alias_pattern(q["aliases"])
        p = parsed[h["course_id"]]
        evidence, field = "", ""
        for fname, lines in (("objectives", p["objectives"]), ("title", [p["title"]]),
                             ("description", p["description"])):
            for s in (lines if fname == "title" else split_sentences(lines)):
                if pat.search(s):
                    evidence, field = s, fname
                    break
            if evidence:
                break
        # 근거 문장이 원문에 그대로 있는지 다시 확인 (원칙 3)
        verified = bool(evidence) and evidence in p["raw"]
        links.append({
            "course_id": h["course_id"],
            "subskill_id": h["subskill_id"],
            "area_id": q["area_id"],
            "area": q["area"],
            "subskill": q["subskill"],
            "search_text": q["search_text"],
            "search_level": LEVEL_KO.get(h["level"], h["level"]),
            "search_rank": h["rank"],
            "match": "certain" if verified and field in ("title", "objectives") else "uncertain",
            "matched_term": pat.search(evidence).group(1) if verified else "",
            "evidence": evidence if verified else "",
            "evidence_field": field if verified else "",
        })

    for name, rows in (("courses.csv", courses), ("course_subskill.csv", links)):
        with (OUT_DIR / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    certain = [l for l in links if l["match"] == "certain"]
    print(f"과정 {len(courses)}개, 연결 {len(links)}건 (근거 있음 {len(certain)}, 불확실 {len(links) - len(certain)})")
    covered = {l["subskill_id"] for l in certain}
    missing = [f"{k} {v['subskill']}" for k, v in queries.items() if k not in covered]
    print(f"근거 있는 과정이 1개 이상인 세부 능력: {len(covered)}/{len(queries)}")
    if missing:
        print("근거 있는 과정이 없는 세부 능력:", ", ".join(missing))


if __name__ == "__main__":
    main()
