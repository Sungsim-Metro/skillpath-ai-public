"""수집한 교육과정 원문을 전처리해 과정 표와 과정↔세부 능력 연결표를 만든다.

입력:  data/raw/courses/*.md, data/raw/courses/search_hits.csv, pipeline/courses/queries.csv
출력:  data/courses/courses.csv          과정 1개당 1행 (설명, 학습 목표, 소요 시간, 평점 등)
       data/courses/course_subskill.csv  과정 ↔ 세부 능력 연결과 근거 문장

직무(--job):
- 클라우드(기본)는 두 표를 새로 만든다.
- 다른 직무(예: ai_engineer)는 queries_<직무>.csv, search_hits_<직무>.csv 로 만든 결과를 기존 표에 덧붙인다.
  courses.csv 에 없는 과정만 더하고(이미 있는 과정의 행은 그대로 둔다), course_subskill.csv 는 그 직무의 세부 능력 행만 바꾼다.
  클라우드 원문(data/raw/courses/ 의 11/13 수집분)이 없는 PC에서도 AI 과정만 더할 수 있게 하려는 것이다.

연결 규칙 (전부 코드로 판단, LLM 없음):
1. 세부 능력 검색어로 검색했을 때 상위에 나온 과정만 그 세부 능력 후보가 된다.
2. 학습 목표 → 과정 제목 → 설명 순서로, 세부 능력 별칭이 처음 나오는 문장을 근거로 붙인다.
3. 근거 문장이 원문 파일에 그대로 있는지 다시 검사한다.
4. 학습 목표나 제목에서 근거를 찾으면 match=certain.
   설명에서만 찾았거나 근거가 없으면 match=uncertain.
   (설명에는 "Windows or Linux desktops"처럼 지나가며 언급한 단어가 많아서, 그것만으로는 그 능력을 가르친다고 보지 않는다.)
"""

import argparse
import csv
import re
from pathlib import Path

from collect_courses import DEFAULT_JOB, JOB_CHOICES, RAW_DIR, hits_file, queries_file

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "courses"
COURSES_FILE = OUT_DIR / "courses.csv"
LINKS_FILE = OUT_DIR / "course_subskill.csv"

SECTION_HEADS = [
    "Description", "Objectives", "AWS Services", "Intended Audience",
    "Recommended previous knowledge", "Ratings", "Course outline", "Outline",
]
# 과정 제목 위에 뜨는 사이트 안내 줄(두 번째는 2026-11-19 수집 때 새로 생겼다)
BANNER_PREFIXES = ("Welcome to AWS Skill Builder", "New: Tell Skill Builder")
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


def parse_rating(sec: dict) -> tuple[str, str]:
    """상세 페이지의 별점 평균과 평가 수를 (평균, 개수) 글자로 돌려준다. 못 읽으면 둘 다 빈 칸(2026-11-20).

    Ratings 절의 'average rating of 4.6 out of 5', 'total number of reviews 11143 reviews' 를 먼저 읽고,
    없으면 제목 아래의 '4.6 (11143)' 줄을 읽는다. 'Rated 5 out of 5' 는 반올림한 값이라 쓰지 않는다.
    평가가 아직 없는 과정은 페이지에 이 줄들이 없다. 그럴듯한 값으로 채우지 않는다(원칙 4).
    """
    avg = count = None
    for line in sec.get("Ratings", []):
        m = re.fullmatch(r"average rating of (\d(?:\.\d+)?) out of 5", line)
        if m:
            avg = m.group(1)
        m = re.fullmatch(r"total number of reviews ([\d,]+) reviews?", line)
        if m:
            count = m.group(1)
    if avg is None or count is None:
        for line in sec.get("_head", []):
            m = re.fullmatch(r"(\d(?:\.\d+)?) \(([\d,]+)\)", line)
            if m:
                avg, count = m.groups()
                break
    if avg is None or count is None:
        return "", ""
    value, total = float(avg), int(count.replace(",", ""))
    if not (0 < value <= 5 and total > 0):
        return "", ""
    return f"{value:.1f}", str(total)


RATING_FIELDS = ["rating_avg", "rating_count"]


def fill_ratings() -> None:
    """과정 표(courses.csv)의 평점 두 열만 원문에서 다시 읽어 채운다. 행과 다른 열은 그대로 둔다.

    과정 목록, 과정↔세부 능력 연결표, 임베딩을 건드리지 않고 평점만 갱신하려는 것이다.
    원문이 없는 과정은 이미 적힌 값을 그대로 둔다.
    """
    rows = read_table(COURSES_FILE)
    if not rows:
        raise SystemExit(f"과정 표가 없습니다: {COURSES_FILE}")
    before = [{k: v for k, v in row.items() if k not in RATING_FIELDS} for row in rows]
    read = rated = 0
    for row in rows:
        path = RAW_DIR / f"{row['course_id']}.md"
        if not path.exists():
            for name in RATING_FIELDS:
                row.setdefault(name, "")
            continue
        read += 1
        _, body = split_front_matter(path.read_text(encoding="utf-8"))
        row["rating_avg"], row["rating_count"] = parse_rating(sections(body))
        rated += bool(row["rating_avg"])
    if before != [{k: v for k, v in row.items() if k not in RATING_FIELDS} for row in rows]:
        raise SystemExit("평점 말고 다른 열이 바뀌었습니다. 저장하지 않습니다")
    fields = [k for k in rows[0] if k not in RATING_FIELDS] + RATING_FIELDS
    write_table(COURSES_FILE, rows, fields)
    have = sum(bool(row["rating_avg"]) for row in rows)
    print(f"과정 {len(rows)}개 중 원문을 읽은 과정 {read}개, 그중 평점이 있는 과정 {rated}개. 표에 평점이 있는 과정 {have}개")


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


def read_table(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_table(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def merge_job(old_courses: list[dict], old_links: list[dict], courses: list[dict], links: list[dict],
              subskill_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """기존 표에 한 직무의 결과를 덧붙인다. 과정은 없는 것만 더하고, 연결은 그 직무의 세부 능력 행만 바꾼다."""
    have = {c["course_id"] for c in old_courses}
    merged_courses = old_courses + [c for c in courses if c["course_id"] not in have]
    merged_links = [l for l in old_links if l["subskill_id"] not in subskill_ids] + links
    return merged_courses, merged_links


def main(job: str = DEFAULT_JOB):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queries = {q["subskill_id"]: q for q in csv.DictReader(queries_file(job).open(encoding="utf-8"))}
    hits = list(csv.DictReader(hits_file(job).open(encoding="utf-8")))

    levels_by_course = {}
    for h in hits:
        levels_by_course.setdefault(h["course_id"], set()).add(h["level"])

    courses, parsed = [], {}
    # 이 직무의 검색에 나온 과정만 읽는다(같은 폴더에 다른 직무 원문이 섞여 있다)
    for path in sorted(RAW_DIR / f"{cid}.md" for cid in levels_by_course):
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = split_front_matter(raw)
        sec = sections(body)
        head = sec.get("_head", [])
        title = next((l for l in head if not l.startswith(BANNER_PREFIXES)), "")
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
        row["rating_avg"], row["rating_count"] = parse_rating(sec)
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

    if not courses:
        raise SystemExit(f"원문이 없습니다: {RAW_DIR} 에 {hits_file(job).name} 의 과정 파일이 하나도 없습니다")
    course_fields, link_fields = list(courses[0].keys()), list(links[0].keys()) if links else []
    if job == DEFAULT_JOB:
        write_table(COURSES_FILE, courses, course_fields)
        write_table(LINKS_FILE, links, link_fields)
    else:
        old_courses, old_links = read_table(COURSES_FILE), read_table(LINKS_FILE)
        added = len({c["course_id"] for c in courses} - {c["course_id"] for c in old_courses})
        all_courses, all_links = merge_job(old_courses, old_links, courses, links, set(queries))
        # 예전 표에 없던 열(평점)이 새 과정에 있으면 맨 뒤에 더한다. 예전 행의 그 칸은 비워 둔다
        old_fields = list(old_courses[0].keys()) if old_courses else []
        write_table(COURSES_FILE, all_courses, old_fields + [k for k in course_fields if k not in old_fields])
        write_table(LINKS_FILE, all_links, list(old_links[0].keys()) if old_links else link_fields)
        print(f"[{job}] 기존 표에 덧붙임: 새 과정 {added}개 (전체 {len(all_courses)}개), 연결 전체 {len(all_links)}건")

    certain = [l for l in links if l["match"] == "certain"]
    print(f"과정 {len(courses)}개, 연결 {len(links)}건 (근거 있음 {len(certain)}, 불확실 {len(links) - len(certain)})")
    covered = {l["subskill_id"] for l in certain}
    missing = [f"{k} {v['subskill']}" for k, v in queries.items() if k not in covered]
    print(f"근거 있는 과정이 1개 이상인 세부 능력: {len(covered)}/{len(queries)}")
    if missing:
        print("근거 있는 과정이 없는 세부 능력:", ", ".join(missing))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", choices=JOB_CHOICES, default=DEFAULT_JOB,
                    help="직무(기본: 클라우드, 표를 새로 만든다). 다른 직무는 기존 표에 덧붙인다")
    ap.add_argument("--ratings-only", action="store_true",
                    help="과정 표의 평점 두 열만 원문에서 다시 읽어 채운다(행과 다른 열은 그대로)")
    args = ap.parse_args()
    if args.ratings_only:
        fill_ratings()
    else:
        main(args.job)
