"""AWS Skill Builder 교육과정 원문 수집기.

팀에 공유된 Playwright 예시 크롤러(crawler.py)를 바탕으로 고쳤다.
- 원본의 시작 주소(explore.skillbuilder.aws/learn)는 지금 홈으로 넘어가서 과정이 잡히지 않는다.
  새 검색 주소(skillbuilder.aws/search)를 쓴다.
- 검색 결과는 무한 스크롤이 아니라 페이지 단위라서, 스크롤 대신 세부 능력별 검색어로 상위 결과만 가져온다.
- 카드 요약이 아니라 과정 상세 페이지 본문 전체를 원문으로 저장한다(추천 근거 문장의 출처).

사용법:
    pip install playwright pandas && playwright install chromium
    python pipeline/courses/collect_courses.py            # 검색 + 상세 원문 수집
    python pipeline/courses/collect_courses.py --limit 2  # 시험용: 검색어 2개만
    python pipeline/courses/collect_courses.py --job ai_engineer  # AI 세부 능력(queries_ai_engineer.csv)

출력 (data/raw/는 저장소에 올리지 않는다):
    data/raw/courses/search_hits.csv   어떤 검색어·난이도로 어떤 과정이 나왔는지
                                       (클라우드 외 직무는 search_hits_<직무 코드>.csv)
    data/raw/courses/<course_id>.md    과정 상세 페이지 원문 (출처 주소와 수집일 포함)
"""

import argparse
import asyncio
import csv
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "courses"
DEFAULT_JOB = "cloud_engineer"
JOB_CHOICES = ("cloud_engineer", "ai_engineer")  # backend/jobs.py 의 ANALYSIS_JOBS 와 같게 둔다


def job_name(name: str, job: str) -> str:
    """직무별 파일 이름. 클라우드는 예전 이름, 다른 직무는 이름 뒤에 직무 코드를 붙인다(backend/jobs.py 의 job_file 과 같은 규칙)."""
    if job == DEFAULT_JOB:
        return name
    stem, _, suffix = name.rpartition(".")
    return f"{stem}_{job}.{suffix}"


def queries_file(job: str = DEFAULT_JOB) -> Path:
    return Path(__file__).with_name(job_name("queries.csv", job))


def hits_file(job: str = DEFAULT_JOB) -> Path:
    return RAW_DIR / job_name("search_hits.csv", job)

SEARCH_URL = (
    "https://skillbuilder.aws/search?page=1&typeId=digital_course"
    "&accessTier=free&courseLevel={level}&searchText={text}"
)
# 신입 페르소나 기준: 기초와 중급만 본다.
LEVELS = ["fundamental", "intermediate"]
TOP_N = 3            # 검색어·난이도마다 상위 몇 개를 가져올지
REQUEST_GAP_MS = 2000  # 요청 사이 대기. 사이트에 부담을 주지 않게 한 번에 한 페이지만 연다.

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("collect_courses")

COURSE_ID_RE = re.compile(r"/learn/([A-Z0-9]{10})/([^/?#]+)")


def course_key(url: str):
    """과정 주소에서 (과정 ID, 검색 추적 파라미터를 뺀 주소)를 돌려준다."""
    m = COURSE_ID_RE.search(url)
    if not m:
        return None, None
    parts = urlsplit(url)
    return m.group(1), f"{parts.scheme}://{parts.netloc}{parts.path}"


async def search(page, text: str, level: str):
    url = SEARCH_URL.format(level=level, text=quote(text))
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    try:
        await page.wait_for_selector("a[href*='/learn/']", timeout=20000)
    except Exception:
        log.warning("검색 결과 없음: %s (%s)", text, level)
        return []
    await page.wait_for_timeout(1500)
    cards = await page.eval_on_selector_all(
        "a[href*='/learn/']",
        "as => as.map(a => ({url: a.href, title: (a.innerText || '').trim().split('\\n')[0]}))",
    )
    hits, seen = [], set()
    for card in cards:
        cid, clean = course_key(card["url"])
        if cid and cid not in seen:
            seen.add(cid)
            hits.append({"course_id": cid, "url": clean, "title": card["title"]})
    return hits[:TOP_N]


async def fetch_detail(page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    try:
        await page.wait_for_selector("text=Description", timeout=20000)
    except Exception:
        log.warning("상세 본문을 찾지 못함: %s", url)
    await page.wait_for_timeout(1500)
    main = await page.query_selector("main")
    return await (main.inner_text() if main else page.inner_text("body"))


async def run(limit: int | None, job: str = DEFAULT_JOB):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    queries = list(csv.DictReader(queries_file(job).open(encoding="utf-8")))
    if limit:
        queries = queries[:limit]
    today = date.today().isoformat()
    from playwright.async_api import async_playwright  # 전처리(build_courses.py)가 이 파일의 이름 규칙만 쓸 때는 필요 없다

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="en-US", viewport={"width": 1440, "height": 1000})
        page = await context.new_page()

        # 1) 세부 능력별 검색
        hits = []
        for q in queries:
            for level in LEVELS:
                found = await search(page, q["search_text"], level)
                log.info("%s %-28s %-12s → %d건", q["subskill_id"], q["search_text"], level, len(found))
                for rank, h in enumerate(found, 1):
                    hits.append({**h, "subskill_id": q["subskill_id"], "search_text": q["search_text"],
                                 "level": level, "rank": rank, "collected_at": today})
                await page.wait_for_timeout(REQUEST_GAP_MS)

        with hits_file(job).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["subskill_id", "search_text", "level", "rank",
                                              "course_id", "title", "url", "collected_at"])
            w.writeheader()
            w.writerows(hits)

        # 2) 과정마다 상세 원문 한 번씩
        courses = {h["course_id"]: h for h in hits}
        log.info("검색 결과 %d건, 서로 다른 과정 %d개", len(hits), len(courses))
        for i, (cid, h) in enumerate(courses.items(), 1):
            out = RAW_DIR / f"{cid}.md"
            if out.exists():
                log.info("[%d/%d] 이미 있음: %s", i, len(courses), cid)
                continue
            body = await fetch_detail(page, h["url"])
            out.write_text(
                f"---\ncourse_id: {cid}\nsource_url: {h['url']}\ncollected_at: {today}\n"
                f"source: AWS Skill Builder 과정 상세 페이지\n---\n\n{body}\n",
                encoding="utf-8",
            )
            log.info("[%d/%d] 저장: %s (%d자)", i, len(courses), cid, len(body))
            await page.wait_for_timeout(REQUEST_GAP_MS)

        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="시험용: 앞에서부터 검색어 몇 개만")
    ap.add_argument("--job", choices=JOB_CHOICES, default=DEFAULT_JOB, help="세부 능력 검색어를 고를 직무(기본: 클라우드)")
    args = ap.parse_args()
    asyncio.run(run(args.limit, args.job))
