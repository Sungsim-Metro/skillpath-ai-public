"""로켓펀치 공고를 받아 contracts/ 의 ① 공고(posting) 형식으로 바꾼다.

사용:
  python -m pipeline.rocketpunch codes                       # 직군 코드 목록
  python -m pipeline.rocketpunch search -k 클라우드 --codes <코드> --size 20
  python -m pipeline.rocketpunch fetch 159620 159227 ...     # 상세를 받아 본문 필터 후 posting 저장
  python -m pipeline.rocketpunch collect --codes <코드> -n 10  # 목록 → 상세 → 본문 필터, 통과 10건까지

- 원본 응답은 data/raw/rp-<jobId>.raw.json 에 저장한다(저장소에 올리지 않는다).
- 이미 받은 공고는 API 를 다시 부르지 않는다. 429 가 오면 멈추고, 다시 실행하면 이어서 받는다.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
KST = timezone(timedelta(hours=9))
BASE = "https://openapi.rocketpunch.com/v1"  # 문서의 /api/v1 은 404 라서 /v1 을 쓴다
# 본문 필드 순서. 필드 이름과 내용이 다른 공고가 있어 라벨 없이 본문만 잇는다.
TEXT_FIELDS = ("description", "duty", "minimumQualification", "preferredQualification", "benefit")
DEFAULT_TERMS = ("aws",)  # 본문 필터 기본 단어(소문자 비교). 서비스가 AWS 교육 추천이라 aws 만 쓴다. "클라우드"는 게임 공고도 통과시켜 뺐다.


def load_env_file() -> None:
    """루트 .env 를 환경변수로 읽는다. 값은 출력하지 않는다."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")  # 같은 이름이 둘이면 나중 줄이 이긴다(python-dotenv 와 같음)
    for key, value in values.items():
        if not os.environ.get(key):  # 이미 값이 있는 환경변수는 덮지 않는다
            os.environ[key] = value


def api_get(path: str, params: dict | None = None) -> dict:
    """GET 1회. 429 면 기다리지 않고 Retry-After 를 알리고 종료한다. 한도 헤더는 stderr 에 남긴다."""
    key = os.getenv("ROCKETPUNCH_API_KEY")
    if not key:
        raise SystemExit(".env 에 ROCKETPUNCH_API_KEY 가 없습니다.")
    url = f"{BASE}{path}" + (f"?{urlencode(params, doseq=True)}" if params else "")
    req = Request(url, headers={"X-RP-API-Key": key, "Accept-Language": "ko"})
    try:
        with urlopen(req, timeout=20) as res:
            limits = {k: v for k, v in res.headers.items() if k.lower().startswith("x-rp-ratelimit")}
            print(f"[한도] {path}: {limits}", file=sys.stderr)
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 429:
            raise SystemExit(f"호출 한도 초과(429). {e.headers.get('Retry-After', '?')}초 뒤에 다시 실행하세요.")
        raise SystemExit(f"HTTP {e.code} {path}: {e.read().decode('utf-8', 'replace')[:300]}")
    except URLError as e:
        raise SystemExit(f"연결 실패: {e.reason}")


def clean(text: str | None) -> str:
    """HTML 태그와 엔티티를 지우고 공백을 정리한다. 빈 값은 빈 문자열."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d)>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def to_posting(raw: dict, role: str = "클라우드 엔지니어") -> dict:
    """로켓펀치 상세 응답 → posting(실습 노트북의 표준 필드 + url). role 은 직무가 하나(M2)라 인자로 받는다."""
    company = raw.get("company") or {}
    started = raw.get("startAt") or raw.get("createdAt")  # 게시일. UTC 로 오므로 한국시간 날짜로 바꾼다
    return {
        "id": f"rp-{raw['jobId']}",
        "company": company.get("name") or "",
        "title": clean(raw["title"]),
        "industry": company.get("industry"),  # 산업 코드 그대로(예: FINANCE_ETC)
        "role": role,
        "career": ",".join(raw.get("seniorities") or []) or None,  # 숙련도 원본. 분석에는 쓰지 않는다
        "region": None,  # 로켓펀치 상세 응답에 지역 필드가 없다
        "posted_at": (
            datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone(KST).date().isoformat()
            if started else None
        ),
        "text": "\n".join(p for p in (clean(raw.get(f)) for f in TEXT_FIELDS) if p),
        "url": raw.get("webUrl"),
    }


def matched_terms(posting: dict, terms=DEFAULT_TERMS) -> list[str]:
    """제목+본문에 나온 필터 단어. 비어 있으면 직무와 무관한 공고일 가능성이 크다."""
    haystack = (posting["title"] + "\n" + posting["text"]).lower()
    # 영문 단어는 단어 경계로 비교한다(aws 가 laws 에 걸리지 않게). 한글은 부분 일치.
    return [t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", haystack)]


def load_or_fetch(job_id: str) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"rp-{job_id}.raw.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raw = api_get(f"/jobs/{job_id}")
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw


def list_page(params: dict) -> list:
    """목록 1쪽. 목록 호출도 job.read 한도를 쓰므로 받은 결과를 저장해 두고 다시 쓴다.
    새로 받으려면 data/raw/list-*.json 을 지운다."""
    name = "-".join(f"{k}={'+'.join(map(str, v)) if isinstance(v, list) else v}" for k, v in sorted(params.items()))
    path = RAW_DIR / f"list-{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    res = api_get("/jobs", params)
    items = res["items"]
    print(f"[목록] 전체 {res['totalItems']}건 중 {len(items)}건 ({params})", file=sys.stderr)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def cmd_codes(_args) -> None:
    for c in api_get("/codes/job-categories"):
        print(f"{c['code']}\t{c['displayName']}")


def cmd_search(args) -> None:
    params = {"pageSize": args.size, "page": args.page, "sort": args.sort}
    if args.keyword:
        params["keyword"] = args.keyword
    if args.codes:
        params["jobCategories"] = args.codes
    listing = api_get("/jobs", params)
    print(f"전체 {listing['totalItems']}건 / {listing['totalPages']}쪽, 이번 {len(listing['items'])}건 (요청 {params})")
    for it in listing["items"]:
        print(f"{it['jobId']}\t{it.get('jobCategory')}\t{it['company']['name']}\t{it['title'][:40]}")
    print("\njobId:", " ".join(str(it["jobId"]) for it in listing["items"]))


def process(job_id, role: str, terms) -> bool:
    """상세를 받아(있으면 재사용) 본문 필터를 적용하고, 통과하면 posting 을 저장한다."""
    posting = to_posting(load_or_fetch(str(job_id)), role)
    hits = matched_terms(posting, terms)
    out = RAW_DIR / f"{posting['id']}.posting.json"
    if hits:
        out.write_text(json.dumps(posting, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        out.unlink(missing_ok=True)  # 필터 기준이 바뀌었을 때 예전 통과본을 지운다
    print(f"{'통과' if hits else '탈락'}\t{posting['id']}\t필터 {hits or '없음'}\t본문 {len(posting['text'])}자\t{posting['title'][:40]}")
    return bool(hits)


def cmd_fetch(args) -> None:
    for job_id in args.ids:
        process(job_id, args.role, args.terms)


def cmd_collect(args) -> None:
    """목록 → 상세 → 본문 필터. 통과가 --n 건이 되거나 429 가 오면 멈춘다. 다시 실행하면 이어서 한다."""
    kept = seen = 0
    for page in range(1, args.pages + 1):
        params = {"pageSize": args.size, "page": page, "sort": args.sort}
        if args.keyword:
            params["keyword"] = args.keyword
        if args.codes:
            params["jobCategories"] = args.codes
        items = list_page(params)
        for it in items:
            seen += 1
            kept += process(it["jobId"], args.role, args.terms)
            if kept >= args.n:
                break
        if kept >= args.n or not items:
            break
    print(f"\n후보 {seen}건 확인, 통과 {kept}건, 탈락 {seen - kept}건 (목표 {args.n}건)")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_env_file()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("codes").set_defaults(run=cmd_codes)
    s = sub.add_parser("search")
    s.add_argument("-k", "--keyword")
    s.add_argument("--codes", nargs="+", help="API 직군 코드(jobCategories)")
    s.add_argument("--sort", default="DATE_DESC", choices=["RELEVANCE_DESC", "DATE_DESC", "POPULARITY_DESC"])
    s.add_argument("--size", type=int, default=10, help="문서는 50이라지만 실제 최대는 10")
    s.add_argument("--page", type=int, default=1)
    s.set_defaults(run=cmd_search)
    f = sub.add_parser("fetch")
    f.add_argument("ids", nargs="+")
    c = sub.add_parser("collect", help="목록 → 상세 → 본문 필터, 통과 n건까지")
    c.add_argument("-k", "--keyword")
    c.add_argument("--codes", nargs="+", help="API 직군 코드(jobCategories)")
    c.add_argument("--sort", default="DATE_DESC", choices=["RELEVANCE_DESC", "DATE_DESC", "POPULARITY_DESC"])
    c.add_argument("--size", type=int, default=10, help="문서는 50이라지만 실제 최대는 10")
    c.add_argument("--pages", type=int, default=1)
    c.add_argument("-n", type=int, default=10, help="통과 목표 건수")
    for sp, run in ((f, cmd_fetch), (c, cmd_collect)):
        sp.add_argument("--role", default="클라우드 엔지니어", help="posting 의 role(직무 이름)")
        sp.add_argument("--terms", nargs="+", default=list(DEFAULT_TERMS), help="본문 필터 단어")
        sp.set_defaults(run=run)
    args = p.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
