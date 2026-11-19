"""로켓펀치 공고를 받아 contracts/ 의 ① 공고(posting) 형식으로 바꾼다.

사용:
  python -m pipeline.rocketpunch codes                       # 직군 코드 목록
  python -m pipeline.rocketpunch search -k 클라우드 --codes <코드> --size 20
  python -m pipeline.rocketpunch fetch 159620 159227 ...     # 상세를 받아 직무 필터 후 posting 저장
  python -m pipeline.rocketpunch collect --codes <코드> -n 10  # 목록 → 제목 선판정 → 상세 → 직무 필터, 통과 10건까지
  python -m pipeline.rocketpunch collect -k Kubernetes --codes dev --pages 5 --any-title -n 999
                                                             # 제목 선판정 없이 상세까지 받는다(AWS 언급 공고를 찾을 때)
  python -m pipeline.rocketpunch export-eval --n 10 --min-aws 4  # 채점용 공고를 data/raw/eval/ 로 내보낸다
  python -m pipeline.rocketpunch add-ids -q AI 159001 159002 ...   # 사이트 검색에서 모은 번호를 중복 없이 기록한다
  python -m pipeline.rocketpunch fetch --manual                    # 기록한 번호 중 아직 안 받은 것을 받는다(429 면 멈춤, 다시 실행하면 이어서)
  python -m pipeline.rocketpunch dedupe                            # 같은 회사·같은 제목의 오래된 공고는 최신 것만 남긴다(호출 없음)

- 제목 필터를 통과한 검토 후보는 data/raw/rp-<id>_<회사>_<제목>.posting.json 으로 저장한다.
- 원본 응답은 data/raw/rp-<jobId>.raw.json 에 저장한다(저장소에 올리지 않는다).
- ROCKETPUNCH_S3_BUCKET 이 있으면 수집한 목록·상세 응답을 S3에 보관한다.
- DynamoDB가 설정된 fetch·collect는 지정한 직무의 postings-reviewed 공고로 수요를 집계한다.
- python -m pipeline.rocketpunch upload --dry-run 으로 기존 파일 업로드를 확인한다.
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

from backend.jobs import ANALYSIS_JOBS, REVIEWED_DOMAINS
from pipeline.reviewed_postings import DOMAINS, RAW_DIR, reviewed_paths
from pipeline.storage import S3Target, maybe_upload, target_from_env, upload_json

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
BASE = "https://openapi.rocketpunch.com/v1"  # 문서의 /api/v1 은 404 라서 /v1 을 쓴다
# 본문 필드와 칸 제목. text 에는 칸마다 제목을 한 줄에 단독으로 쓰고 다음 줄부터 본문을 쓴다.
# ai/sections.py 가 이 제목으로 칸(자격요건·주요업무·우대사항)을 찾아 가중치를 건다(실습 노트북의 [주요업무] 방식).
# 주의: 로켓펀치는 필드 이름과 내용이 다른 공고가 있다(예: 159625 는 duty 에 회사 소개). 그런 공고는 칸이 틀릴 수 있다.
SECTIONS = (
    ("description", "포지션소개"),
    ("duty", "주요업무"),
    ("minimumQualification", "자격요건"),
    ("preferredQualification", "우대사항"),
    ("benefit", "혜택및복지"),
)
# 직무 필터: 제목에 이 단어가 있으면 클라우드 계열 직무로 본다(소문자 비교, 영문은 단어 경계).
# 이전에는 본문에 aws 가 있는지로 걸렀는데, 그러면 남은 공고의 AWS 수요율이 계산상 항상 100% 가 되고
# 직무가 맞아도 aws 를 안 쓴 공고(SRE·DevOps 등)는 버려진다(2026-11-15 팀 피드백). 이제 aws 는 거르는 조건이 아니라
# 수요를 세는 대상이다. 단어 목록은 사람이 고치는 값이다(--terms 로 바꿔 시험할 수 있다).
ROLE_TERMS = ("클라우드", "cloud", "인프라", "infra", "infrastructure", "devops", "sre", "site reliability",
              "mlops", "시스템 엔지니어")


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


class JobNotFound(SystemExit):
    """404: 없는 공고(번호 오타나 삭제된 공고). 잡지 않으면 예전처럼 메시지를 내고 종료하고,
    fetch 는 이것을 잡아 그 번호만 기록하고 건너뛴다(번호 하나 때문에 전체 받기가 멈추지 않게)."""


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
        message = f"HTTP {e.code} {path}: {e.read().decode('utf-8', 'replace')[:300]}"
        raise (JobNotFound if e.code == 404 else SystemExit)(message)
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


def _text(raw: dict) -> str:
    """칸마다 `[제목]` 한 줄 + 본문. 내용이 빈 칸은 제목도 쓰지 않는다."""
    blocks = []
    for field, heading in SECTIONS:
        body = clean(raw.get(field))
        if body:
            blocks.append(f"[{heading}]\n{body}")
    return "\n".join(blocks)


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
        "text": _text(raw),
        "url": raw.get("webUrl"),
    }


def matched_terms(posting: dict, terms=ROLE_TERMS, fields=("title",)) -> list[str]:
    """fields(기본은 제목)에 나온 단어. 직무 필터는 제목만, 본문까지 보려면 fields=("title", "text")."""
    haystack = "\n".join(posting[f] for f in fields).lower()
    # 영문 단어는 단어 경계로 비교한다(sre 가 presrc 같은 글자에 걸리지 않게). 한글은 부분 일치.
    return [t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", haystack)]


def write_json(path: Path, data: dict | list) -> None:
    """JSON을 UTF-8, 줄바꿈 LF로 저장한다.

    S3 키는 파일 바이트의 해시다. 줄바꿈을 OS 기본값에 맡기면 Windows에서는 CRLF가 되어
    같은 응답이 다른 키로 올라간다."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def load_or_fetch(job_id: str) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"rp-{job_id}.raw.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = api_get(f"/jobs/{job_id}")
        write_json(path, raw)
    maybe_upload(path)
    return raw


def load_listing(params: dict) -> dict:
    """목록 응답 전체를 캐시한다. 예전에 저장한 items 배열 형식도 그대로 읽는다.

    목록 호출도 job.read 한도를 쓰므로 받은 결과를 저장해 두고 다시 쓴다.
    새로 받으려면 data/raw/list-*.json 을 지운다."""
    name = "-".join(f"{k}={'+'.join(map(str, v)) if isinstance(v, list) else v}" for k, v in sorted(params.items()))
    path = RAW_DIR / f"list-{name}.json"
    if path.exists():
        res = json.loads(path.read_text(encoding="utf-8"))
    else:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        res = api_get("/jobs", params)
        write_json(path, res)
    maybe_upload(path)
    return {"items": res} if isinstance(res, list) else res


def list_page(params: dict) -> list:
    """수집기에 필요한 items만 돌려준다. 전체 응답 저장은 load_listing이 맡는다."""
    res = load_listing(params)
    items = res["items"]
    total = res.get("totalItems", "미상(기존 캐시)")
    print(f"[목록] 전체 {total}건 중 {len(items)}건 ({params})", file=sys.stderr)
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
    listing = load_listing(params)
    total = listing.get("totalItems", "미상(기존 캐시)")
    pages = listing.get("totalPages", "미상")
    print(f"전체 {total}건 / {pages}쪽, 이번 {len(listing['items'])}건 (요청 {params})")
    for it in listing["items"]:
        print(f"{it['jobId']}\t{it.get('jobCategory')}\t{it['company']['name']}\t{it['title'][:40]}")
    print("\njobId:", " ".join(str(it["jobId"]) for it in listing["items"]))


def _slug(text: str, limit: int) -> str:
    """파일 이름에 쓸 수 있는 읽기 쉬운 조각. [회사] 같은 꼬리표와 윈도우 금지 문자를 빼고 공백은 -로 잇는다."""
    text = re.sub(r"\[[^\]]*\]|\(주\)|㈜|주식회사", " ", text or "")  # [꼬리표]와 (주)·주식회사는 이름에서 뺀다
    text = re.sub(r'[<>:"/\\|?*·,()\x00-\x1f]+', " ", text)
    return "-".join(text.split())[:limit].strip("-.") or "무제"


def posting_path(posting: dict) -> Path:
    """사람이 열어 볼 posting 파일 이름: rp-159502_한패스_핀테크-IT-인프라-운영-관리-담당-경력.posting.json
    id 가 맨 앞이라 이름만 보고도 공고를 찾을 수 있고, 회사와 제목이 있어 목록에서 구분된다."""
    return RAW_DIR / f"{posting['id']}_{_slug(posting['company'], 12)}_{_slug(posting['title'], 30)}.posting.json"


def _posting_files(posting_id: str) -> list[Path]:
    """한 공고의 posting 파일 전부(예전 이름 rp-<id>.posting.json 포함). id 가 앞부분만 같은 다른 공고는 걸리지 않는다."""
    return [*RAW_DIR.glob(f"{posting_id}_*.posting.json"), *RAW_DIR.glob(f"{posting_id}.posting.json")]


def process(job_id, role: str, terms) -> bool:
    """상세를 받아(있으면 재사용) 직무(제목) 필터를 적용하고, 통과하면 posting 을 저장한다."""
    posting = to_posting(load_or_fetch(str(job_id)), role)
    hits = matched_terms(posting, terms)
    out = posting_path(posting)
    if hits:
        write_json(out, posting)
    for old in _posting_files(posting["id"]):
        if not hits or old != out:
            old.unlink()  # 로컬 후보 갱신 후 예전 이름의 파일을 정리한다
    print(f"{'통과' if hits else '탈락'}\t{posting['id']}\t필터 {hits or '없음'}\t본문 {len(posting['text'])}자\t{posting['title'][:40]}")
    return bool(hits)


MANUAL_FILE = ROOT / "data" / "manual_job_ids.json"  # 사람이 사이트 검색 결과에서 모은 공고 번호(번호만, 원문 없음)


def _manual_data() -> dict:
    return json.loads(MANUAL_FILE.read_text(encoding="utf-8")) if MANUAL_FILE.exists() else {"batches": []}


def _manual_batches() -> list[dict]:
    return _manual_data()["batches"]


def manual_ids() -> list[int]:
    """모아 둔 번호 전부(저장된 순서, 중복 없음). 번호는 처음 나온 검색의 묶음에 한 번만 들어 있다."""
    return list(dict.fromkeys(i for b in _manual_batches() for i in b["ids"]))


def found_by() -> dict[str, list[str]]:
    """공고 번호 → 그 번호가 나온 검색어 목록. 여러 검색에 나온 번호(예: 클라우드와 AI)는 함께 표시한다."""
    data = _manual_data()
    out: dict[str, list[str]] = {}
    for b in data["batches"]:
        for i in b["ids"]:
            queries = out.setdefault(str(i), [])
            if b["query"] not in queries:
                queries.append(b["query"])
    for i, queries in data.get("found_by", {}).items():
        merged = out.setdefault(i, [])
        merged += [q for q in queries if q not in merged]
    return out


def cmd_add_ids(args) -> None:
    """사이트 검색에서 모은 번호를 기록한다. 입력 안의 중복은 하나만 남긴다.
    이미 기록된 번호는 다시 받지 않고 번호도 그대로 두되, 어느 검색에 또 나왔는지(found_by)는 표시한다.
    번호만 저장한다(원문 없음). 어떤 검색어로 언제 모았는지를 같이 남긴다(docs/data-sources.md 의 수동 수집 규칙)."""
    known = set(manual_ids())
    unique = list(dict.fromkeys(args.ids))
    fresh = [i for i in unique if i not in known]
    tags = found_by()
    for i in unique:  # 이미 있던 번호도 이 검색어에서 나왔다고 표시한다
        queries = tags.setdefault(str(i), [])
        if args.query not in queries:
            queries.append(args.query)
    batches = _manual_batches()
    if fresh:
        batches.append({"collected_at": datetime.now(KST).date().isoformat(), "source": "rocketpunch 사이트 검색",
                        "query": args.query, "ids": fresh})
    note = ("사람이 로켓펀치 사이트 검색 결과에서 모은 공고 번호(번호만, 원문 없음). fetch --manual 로 상세를 받는다. "
            "번호는 한 번만 기록하고, found_by 에 그 번호가 나온 검색어를 모두 적는다.")
    MANUAL_FILE.write_text(json.dumps({"note": note, "batches": batches, "found_by": tags}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    both = len(unique) - len(fresh)
    print(f"입력 {len(args.ids)}개(입력 안 중복 {len(args.ids) - len(unique)}개 제외) → 새 번호 {len(fresh)}개 기록, "
          f"이미 있던 번호 {both}개는 다시 기록하지 않고 '{args.query}' 에서도 나왔다고만 표시. 모아 둔 번호 전체 {len(known) + len(fresh)}개")


def _dup_key(raw: dict) -> tuple[str, str]:
    return ((raw.get("company") or {}).get("name") or "", clean(raw.get("title")).lower())


def older_duplicates(raws: list[dict]) -> dict[str, str]:
    """같은 회사·같은 제목인데 번호가 다른 공고는 가장 최신(startAt 이 늦은 것, 같으면 번호가 큰 것) 하나만 남긴다.
    반환: {밀려난 jobId: 남긴 jobId}. 제목이 비어 있으면 같은 공고인지 알 수 없어 묶지 않는다."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in raws:
        groups.setdefault(_dup_key(r), []).append(r)
    dropped: dict[str, str] = {}
    for key, rs in groups.items():
        if len(rs) > 1 and key[1]:
            keep = max(rs, key=lambda r: (r.get("startAt") or r.get("createdAt") or "", int(r["jobId"])))
            dropped.update({str(r["jobId"]): str(keep["jobId"]) for r in rs if r is not keep})
    return dropped


def _all_raws() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(RAW_DIR.glob("rp-*.raw.json"))]


def cmd_dedupe(_args) -> None:
    """받아 둔 원본에서 같은 회사·같은 제목의 오래된 공고를 정리한다(호출 없음).
    원본(raw)은 남기고 오래된 쪽의 posting 파일만 지운다. 무엇을 지웠는지는 data/raw/중복공고.csv 에 남긴다."""
    import csv

    raws = _all_raws()
    by_id = {str(r["jobId"]): r for r in raws}
    dropped = older_duplicates(raws)
    rows = []
    for old, keep in dropped.items():
        for f in _posting_files(f"rp-{old}"):
            f.unlink()
        r_old, r_keep = by_id[old], by_id[keep]
        rows.append([keep, r_keep.get("startAt") or r_keep.get("createdAt"), old, r_old.get("startAt") or r_old.get("createdAt"),
                     (r_keep.get("company") or {}).get("name"), clean(r_keep.get("title"))])
    with (RAW_DIR / "중복공고.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["남긴 번호", "남긴 게시일", "밀려난 번호", "밀려난 게시일", "회사", "제목"])
        w.writerows(rows)
    print(f"받아 둔 원본 {len(raws)}건 중 같은 회사·같은 제목의 오래된 공고 {len(dropped)}건을 정리했습니다(최신 것만 남김).")
    for r in rows[:10]:
        print(f"  남김 {r[0]}({str(r[1])[:10]}) ← 밀려남 {r[2]}({str(r[3])[:10]}) | {r[4]} | {r[5][:30]}")


MISSING_FILE = RAW_DIR / "없는공고.json"  # 404 가 난 번호(번호 오타나 삭제된 공고). 다시 받으려 하지 않는다


def _missing_ids() -> set[int]:
    return set(json.loads(MISSING_FILE.read_text(encoding="utf-8"))) if MISSING_FILE.exists() else set()


def batch_options(args) -> dict | None:
    """테이블 설정이 있으면 API 호출 전에 추출 배치의 실행 설정을 검사한다."""
    table_names = (
        os.getenv("DYNAMODB_EXTRACTIONS_TABLE", "").strip(),
        os.getenv("DYNAMODB_DEMAND_TABLE", "").strip(),
    )
    if not any(table_names):
        return None

    from pipeline.extraction_batch import (
        parse_exclusions, selection_version_for_terms, validate_configuration,
    )

    job_category = getattr(args, "job_category", "cloud_engineer")
    validate_configuration(job_category)
    collected = getattr(args, "collected", None)
    if not collected or not collected.strip():
        raise ValueError("DynamoDB 저장에는 --collected로 수집일 또는 기간을 지정하세요.")
    return {
        "job_category": job_category,
        "collected": collected.strip(),
        "force": getattr(args, "force", False),
        "exclusions": parse_exclusions(getattr(args, "exclude", [])),
        "selection_version": selection_version_for_terms(
            None, getattr(args, "selection_version", None)
        ),
    }


def persist_extractions(options: dict | None) -> None:
    """수집 후보와 별도로 해당 직무의 검토본 전체를 분석한다."""
    if options is None:
        return
    from pipeline.extraction_batch import run_batch

    paths = reviewed_paths(REVIEWED_DOMAINS[options["job_category"]], raw_dir=RAW_DIR, include_dataset=True)
    summary = run_batch(paths, **options)
    print(json.dumps(summary, ensure_ascii=False))
    if not summary["published"]:
        raise SystemExit(1)


def cmd_fetch(args) -> None:
    options = batch_options(args)  # 설정이 틀리면 API 호출 전에 멈춘다
    ids = list(args.ids)
    queries = getattr(args, "queries", None)
    if getattr(args, "manual", False):  # 모아 둔 번호 중 아직 안 받은 것만(받은 것은 호출이 들지 않지만 출력이 길어진다)
        missing = _missing_ids()
        tags = found_by()
        wanted = lambda i: not queries or any(q in queries for q in tags.get(str(i), []))  # --queries 가 있으면 그 검색에서 나온 번호만
        todo = [i for i in manual_ids() if i not in missing and wanted(i) and not (RAW_DIR / f"rp-{i}.raw.json").exists()]
        held = sum(1 for i in manual_ids() if not wanted(i) and not (RAW_DIR / f"rp-{i}.raw.json").exists())
        print(f"모아 둔 번호 {len(manual_ids())}개 중 아직 안 받은 것 {len(todo)}개(상세 호출 {len(todo)}회), "
              f"없는 공고로 기록된 번호 {len(missing)}개는 건너뜀" + (f", --queries 밖이라 보류 {held}개" if queries else ""))
        ids += todo
    if not ids:
        print("받을 번호가 없습니다.")
    for job_id in ids:
        try:
            process(job_id, args.role, args.terms)
        except JobNotFound as e:  # 없는 번호는 기록하고 건너뛴다. 429 같은 다른 종료는 그대로 멈춘다
            print(f"없음\trp-{job_id}\t로켓펀치에 없는 공고(번호 오타나 삭제). 기록하고 건너뜀: {str(e)[:80]}")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            MISSING_FILE.write_text(json.dumps(sorted(_missing_ids() | {int(job_id)})), encoding="utf-8")
    persist_extractions(options)


def cmd_collect(args) -> None:
    """목록 → (제목 선판정) → 상세 → 직무 필터. 통과가 --n 건이 되거나 429 가 오면 멈춘다. 다시 실행하면 이어서 한다.

    목록에는 제목이 이미 있다. 직무 필터가 제목만 보므로 제목이 안 맞는 공고는 상세를 받기 전에 건너뛴다(상세 1회 절약).
    이미 받은 원본이 있으면 호출이 들지 않으니 그대로 처리한다."""
    options = batch_options(args)
    kept = seen = skipped = 0
    for page in range(1, args.pages + 1):
        params = {"pageSize": args.size, "page": page, "sort": args.sort}
        if args.keyword:
            params["keyword"] = args.keyword
        if args.codes:
            params["jobCategories"] = args.codes
        items = list_page(params)
        for it in items:
            seen += 1
            cached = (RAW_DIR / f"rp-{it['jobId']}.raw.json").exists()
            if not getattr(args, "any_title", False) and not cached and not matched_terms({"title": it["title"]}, args.terms):
                skipped += 1
                # 예전 기준으로 통과해 저장된 posting 이 원본 없이 남아 있어도 이제는 탈락이므로 같이 지운다
                for stale in _posting_files(f"rp-{it['jobId']}"):
                    stale.unlink()
                print(f"건너뜀\trp-{it['jobId']}\t제목이 직무 단어에 안 맞아 상세를 받지 않음\t{it['title'][:40]}")
                continue
            kept += process(it["jobId"], args.role, args.terms)
            if kept >= args.n:
                break
        if kept >= args.n or not items:
            break
    print(f"\n후보 {seen}건 확인, 통과 {kept}건, 상세 없이 건너뜀 {skipped}건(호출 {skipped}회 절약), "
          f"그 밖의 탈락 {seen - kept - skipped}건 (목표 {args.n}건)")
    persist_extractions(options)


def cmd_upload(args) -> None:
    """API를 다시 부르지 않고 기존 로컬 공고 JSON을 S3에 올린다."""
    configured = target_from_env()
    bucket = args.bucket or (configured.bucket if configured else None)
    if not bucket:
        raise SystemExit("--bucket 또는 ROCKETPUNCH_S3_BUCKET으로 대상 버킷을 지정하세요.")
    prefix = args.prefix
    if prefix is None:
        prefix = configured.prefix if configured else S3Target(bucket).prefix
    target = S3Target(bucket, prefix)
    patterns = ("list-*.json", "rp-*.raw.json")
    paths = sorted({path for pattern in patterns for path in RAW_DIR.glob(pattern)})
    for domain in DOMAINS:
        paths.extend(reviewed_paths(domain, raw_dir=RAW_DIR))
    uploaded = existing = 0
    for path in paths:
        result = upload_json(path, target, dry_run=args.dry_run)
        if args.dry_run:
            print(f"{path.relative_to(RAW_DIR)} -> s3://{bucket}/{result.key}")
        elif result.uploaded:
            uploaded += 1
        else:
            existing += 1
    if args.dry_run:
        print(f"\n업로드 예정 {len(paths)}개 (실제 업로드 없음)")
    else:
        print(f"\nS3 동기화 완료: 업로드 {uploaded}개, 이미 있음 {existing}개, 대상 {len(paths)}개")


TUNING_FILE = ROOT / "data" / "tuning_ids.json"  # 성진님이 프롬프트를 고칠 때 본 공고 번호(번호만, 원문 없음)
EVAL_DIR = RAW_DIR / "eval"


def cmd_export_eval(args) -> None:
    """검토된 AI·클라우드 공고에서 튜닝 공고와 중복 공고를 빼고 채점용 표본을 고른다."""
    from ai.sections import headings

    domain_for_role = {ANALYSIS_JOBS[job]: domain for job, domain in REVIEWED_DOMAINS.items()}
    domain = domain_for_role[args.role] if args.role else None
    try:
        paths = reviewed_paths(domain, raw_dir=RAW_DIR)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if not paths:
        raise SystemExit("검토된 공고가 없습니다. 채점 표본을 내보내려면 검토 디렉터리를 확인하세요.")
    postings = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    tuned = {str(i) for i in json.loads(TUNING_FILE.read_text(encoding="utf-8"))["ids"]}
    # 기존 재공고 판정에 필요한 메타데이터만 사용하고 내보내는 공고는 검토본 그대로 둔다.
    stale = older_duplicates([
        {"jobId": p["id"].removeprefix("rp-"), "title": p["title"], "company": {"name": p["company"]},
         "startAt": p["posted_at"]} for p in postings
    ])
    excluded = tuned | stale.keys()
    pool = [p for p in postings if p["id"].removeprefix("rp-") not in excluded]
    pool.sort(key=lambda p: (not matched_terms(p, args.terms or ()), -int(p["id"].removeprefix("rp-"))))
    aws_pool = [p for p in pool if matched_terms(p, ["aws"], fields=("title", "text"))]
    aws_ids = {p["id"] for p in aws_pool}
    want_aws = min(args.min_aws, args.n)
    picked = aws_pool[:want_aws]
    taken = {p["id"] for p in picked}
    picked.extend(p for p in pool if p["id"] not in taken)
    picked = picked[:args.n]

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    for old in EVAL_DIR.glob("eval-*"):
        old.unlink()  # 다시 내보내면 이전 결과를 지우고 새로 만든다
    rows = []
    for i, p in enumerate(picked, 1):
        name = f"eval-{i:02d}_{posting_path(p).name.replace('.posting.json', '.json')}"
        (EVAL_DIR / name).write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append([i, p["id"], p["company"], p["title"], p["role"], "있음" if p["id"] in aws_ids else "없음",
                     len(headings(p["text"])), p["url"]])
    import csv
    with (EVAL_DIR / "eval-목록.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "id", "회사", "제목", "직무", "AWS 언급", "칸 제목 수", "링크"])
        w.writerows(rows)
    n_aws = sum(1 for r in rows if r[5] == "있음")
    print(f"튜닝·중복을 제외한 검토 공고 {len(pool)}건 중 {len(rows)}건을 {EVAL_DIR} 에 내보냈습니다. AWS 언급 {n_aws}건.")
    if args.min_aws > args.n:
        print(f"  참고: --min-aws({args.min_aws})가 --n({args.n})보다 커서 AWS 언급 기준을 {args.n}건으로 맞췄습니다.")
    if len(rows) < args.n or n_aws < want_aws:
        print(f"  ⚠ 목표(전체 {args.n}건, AWS 언급 {want_aws}건 이상)에 못 미칩니다: "
              f"{args.n - len(rows)}건, AWS 언급 {max(want_aws - n_aws, 0)}건 부족.")
    for r in rows:
        print(f"  {r[0]:02d} {r[1]} | {r[2]} | {r[3][:34]} | 직무 {r[4]} | AWS {r[5]}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
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
    f.add_argument("ids", nargs="*", type=int)
    f.add_argument("--manual", action="store_true", help="add-ids 로 모아 둔 번호 중 아직 안 받은 것을 받는다")
    f.add_argument("--queries", nargs="+", help="--manual 과 함께: 이 검색어에서 나온 번호만 받는다(나머지는 보류). 예: --queries AWS 클라우드")
    a = sub.add_parser("add-ids", help="사이트 검색에서 모은 공고 번호를 중복 없이 기록한다")
    a.add_argument("-q", "--query", required=True, help="번호를 모은 검색어(예: AWS, 클라우드, AI)")
    a.add_argument("ids", nargs="+", type=int)
    a.set_defaults(run=cmd_add_ids)
    sub.add_parser("dedupe", help="같은 회사·같은 제목의 오래된 공고를 정리한다(호출 없음)").set_defaults(run=cmd_dedupe)
    c = sub.add_parser("collect", help="목록 → 제목 선판정 → 상세 → 직무 필터, 통과 n건까지")
    c.add_argument("-k", "--keyword")
    c.add_argument("--codes", nargs="+", help="API 직군 코드(jobCategories)")
    c.add_argument("--sort", default="DATE_DESC", choices=["RELEVANCE_DESC", "DATE_DESC", "POPULARITY_DESC"])
    c.add_argument("--size", type=int, default=10, help="문서는 50이라지만 실제 최대는 10")
    c.add_argument("--pages", type=int, default=1)
    c.add_argument("-n", type=int, default=10, help="통과 목표 건수")
    u = sub.add_parser("upload", help="기존 로컬 공고 JSON을 S3에 업로드 (API 재호출 없음)")
    u.add_argument("--bucket", help="대상 S3 버킷. 기본값은 ROCKETPUNCH_S3_BUCKET")
    u.add_argument("--prefix", help="S3 경로 접두사. 기본값은 ROCKETPUNCH_S3_PREFIX 또는 skillpath/rocketpunch")
    u.add_argument("--dry-run", action="store_true", help="업로드 경로만 확인")
    u.set_defaults(run=cmd_upload)
    c.add_argument("--any-title", action="store_true", help="제목 선판정을 하지 않고 상세까지 받는다(AWS 언급 공고를 찾을 때)")
    e = sub.add_parser("export-eval", help="채점용 공고를 data/raw/eval/ 로 내보낸다(호출 없음)")
    e.add_argument("--n", type=int, default=10, help="내보낼 건수")
    e.add_argument("--min-aws", type=int, default=4, help="AWS 언급 공고 최소 건수")
    e.add_argument("--role", choices=sorted(ANALYSIS_JOBS.values()), help="검토 직무를 한 가지로 제한")
    e.add_argument("--terms", nargs="+", help="해당 단어가 제목에 있는 검토 공고를 우선 선택")
    e.set_defaults(run=cmd_export_eval)
    for sp, run in ((f, cmd_fetch), (c, cmd_collect)):
        sp.add_argument("--role", default="클라우드 엔지니어", help="posting 의 role(직무 이름)")
        sp.add_argument("--terms", nargs="+", default=list(ROLE_TERMS), help="제목 기준 직무 필터 단어")
        sp.set_defaults(run=run)
    for sp in (f, c):
        sp.add_argument("--job-category", default="cloud_engineer", choices=sorted(ANALYSIS_JOBS),
                        help="검토본을 집계할 분석 직무")
        sp.add_argument("--collected", help="수집일 또는 기간. DynamoDB 저장 시 필수")
        sp.add_argument("--force", action="store_true", help="저장된 동일 입력의 추출 결과도 다시 분석")
        sp.add_argument("--exclude", action="append", default=[], metavar="ID=REASON", help="집계에서 제외할 공고와 사유")
        sp.add_argument("--selection-version", help="검토본 선정 규칙에 기록할 버전")
    args = p.parse_args()
    load_env_file()
    args.run(args)


if __name__ == "__main__":
    main()
