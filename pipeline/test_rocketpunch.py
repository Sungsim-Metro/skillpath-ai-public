"""to_posting 확인. 실행: python -m pipeline.test_rocketpunch"""
import re

from pipeline.rocketpunch import _slug, matched_terms, posting_path, to_posting

RAW = {
    "jobId": 1,
    "title": "클라우드 &amp; 백엔드 엔지니어",
    "company": {"name": "가상회사", "industry": "FINANCE_ETC"},
    "seniorities": ["JUNIOR", "MIDLEVEL"],
    "startAt": "2026-10-26T15:00:00Z",  # UTC 15시 = 한국시간 다음 날 0시
    "webUrl": "https://www.rocketpunch.com/jobs/1",
    "description": "<p>AWS 운영</p><p>&nbsp;</p>",
    "duty": None,
    "minimumQualification": "Python 개발 경험<br/>Linux 이해",
    "preferredQualification": "Docker 우대",
    "benefit": "",
}


def demo():
    p = to_posting(RAW)
    # 실습 노트북의 표준 필드 + url (contracts/README.md ①)
    assert set(p) == {"id", "company", "title", "industry", "role", "career", "region", "posted_at", "text", "url"}
    assert p["id"] == "rp-1" and p["company"] == "가상회사" and p["role"] == "클라우드 엔지니어"
    assert p["industry"] == "FINANCE_ETC" and p["career"] == "JUNIOR,MIDLEVEL" and p["region"] is None
    assert p["posted_at"] == "2026-10-27" and p["url"] == "https://www.rocketpunch.com/jobs/1"
    assert p["title"] == "클라우드 & 백엔드 엔지니어"
    empty = to_posting({**RAW, "seniorities": [], "startAt": None, "webUrl": None})
    assert empty["career"] is None and empty["posted_at"] is None and empty["url"] is None  # 값이 없으면 null
    # 칸마다 제목을 한 줄에 단독으로 쓰고, 빈 칸(duty, benefit)은 제목도 쓰지 않는다
    assert p["text"] == (
        "[포지션소개]\nAWS 운영\n[자격요건]\nPython 개발 경험\nLinux 이해\n[우대사항]\nDocker 우대"
    ), p["text"]
    # ai/sections.py 가 이 제목으로 칸을 찾는다
    from ai.sections import find_section
    assert find_section(p["text"], "Python 개발 경험") == "qualification"
    assert find_section(p["text"], "Docker 우대") == "preference"
    assert find_section(p["text"], "AWS 운영") == "other"  # 포지션소개는 표에 없는 칸이라 other
    assert not re.search(r"<[^>]+>|&\w+;", p["text"])
    # 직무 필터는 제목만 본다: 제목의 '클라우드'는 걸리고, 본문에만 있는 aws 는 직무 판단에 쓰지 않는다
    assert matched_terms(p) == ["클라우드"]
    assert matched_terms(p, ["aws"]) == []
    assert matched_terms(p, ["aws"], fields=("title", "text")) == ["aws"]  # 본문까지 보려면 fields 로 지정
    assert matched_terms(p, ["kafka"]) == []
    assert matched_terms({"title": "laws", "text": "flaws, AWS2"}, ["aws"], fields=("title", "text")) == []  # 단어 경계
    # 제목 기준 판정 예: 클라우드 계열 직무는 통과, 개발·보안 직무는 탈락(본문에 aws 가 있어도)
    for title, ok in (("Site Reliability Engineer (DevOps)", True), ("클라우드·서버 운영 엔지니어", True),
                      ("[한패스] 핀테크 IT 인프라 운영 관리 담당", True), ("MLOps Engineer", True),
                      ("SW·인프라 아키텍트", True), ("[ESTsecurity] 시스템 엔지니어 (경력)", True),
                      ("Node.js 개발자", False), ("시니어 풀스택 개발자", False), ("레드팀 엔지니어", False),
                      ("AI 프로덕트 엔지니어 (RN 앱, Node 서버)", False)):
        assert bool(matched_terms({"title": title, "text": "AWS 경험"})) == ok, title


def names():
    # 사람이 열어 볼 파일 이름: id 가 맨 앞, 회사와 제목이 읽을 수 있게 들어간다
    p = {"id": "rp-159502", "company": "한패스", "title": "[ 한패스 ] 핀테크 IT 인프라 운영 관리 담당 경력"}
    assert posting_path(p).name == "rp-159502_한패스_핀테크-IT-인프라-운영-관리-담당-경력.posting.json", posting_path(p).name
    # 윈도우에서 못 쓰는 문자(/ : ? " 등)와 [꼬리표]는 빠지고, 길이는 제한된다
    assert _slug('Redis/DevOps: "Engineer"?', 40) == "Redis-DevOps-Engineer"
    assert _slug("[KR] Customer On-prem Engineer (DevOps)", 40) == "Customer-On-prem-Engineer-DevOps"
    assert len(_slug("가" * 100, 30)) == 30 and _slug("", 10) == "무제" and _slug("[전부 꼬리표]", 10) == "무제"
    assert _slug("(주)허드슨에이아이", 12) == "허드슨에이아이" and _slug("더선한 주식회사", 12) == "더선한"
    # 같은 회사의 다른 공고는 id 가 달라 이름이 겹치지 않는다
    assert posting_path({**p, "id": "rp-159503"}).name != posting_path(p).name


def export_eval():
    """검토 공고만 직무를 유지한 채 내보내고, 중복·튜닝 공고를 제외한다."""
    import contextlib
    import csv
    import io
    import json
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch

    import pipeline.rocketpunch as rp

    def posting(job_id, title, text, company=None, date="2026-11-17"):
        return {"id": f"rp-{job_id}", "title": title, "text": text, "company": company or f"회사{job_id}",
                "posted_at": date, "url": f"https://example.com/{job_id}"}

    with tempfile.TemporaryDirectory() as directory:
        raw_dir = Path(directory)
        reviewed = raw_dir / "rocketpunch-2026-11-17" / "postings-reviewed"
        eval_dir = raw_dir / "eval"
        tuning_file = raw_dir / "tuning.json"
        roles = {"ai": "AI 엔지니어", "cloud": "클라우드 엔지니어"}

        def write(domain, job):
            path = reviewed / domain / (job["id"] + ".posting.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**job, "role": roles[domain]}), encoding="utf-8")

        shared = posting(4, "소프트웨어 엔지니어", "AWS 운영 경험")
        write("ai", shared)
        write("cloud", shared)
        write("cloud", posting(1, "클라우드 엔지니어", "AWS 운영 경험"))
        write("cloud", posting(2, "인프라 엔지니어", "AWS 경험"))
        write("ai", posting(3, "엔지니어", "모델 학습"))
        write("cloud", posting(5, "플랫폼 개발자", "서비스 배포"))
        write("cloud", posting(6, "플랫폼 개발자", "서비스 배포", company="회사5", date="2025-11-17"))
        unreviewed = {"jobId": 99, "title": "인프라 엔지니어", "description": "AWS 운영", "company": {"name": "회사99"}}
        (raw_dir / "rp-99.raw.json").write_text(json.dumps(unreviewed), encoding="utf-8")
        tuning_file.write_text(json.dumps({"ids": [1]}), encoding="utf-8")
        eval_dir.mkdir()
        (eval_dir / "eval-99_예전.json").write_text("{}", encoding="utf-8")

        def run(n, min_aws, role=None, terms=None):
            args = SimpleNamespace(n=n, min_aws=min_aws, role=role, terms=terms or [])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rp.cmd_export_eval(args)
            with (eval_dir / "eval-목록.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            postings = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(eval_dir.glob("eval-*.json"))]
            return rows, postings, out.getvalue()

        with patch.multiple(rp, RAW_DIR=raw_dir, EVAL_DIR=eval_dir, TUNING_FILE=tuning_file):
            rows, postings, output = run(10, 2)
            assert [p["id"] for p in postings] == ["rp-4", "rp-2", "rp-5", "rp-3"], postings
            assert [p["role"] for p in postings] == [roles["ai"], roles["cloud"], roles["cloud"], roles["ai"]]
            assert [row["AWS 언급"] for row in rows] == ["있음", "있음", "없음", "없음"]
            assert "목표" in output and "부족" in output
            rows, postings, output = run(1, 99, terms=["인프라"])
            assert [p["id"] for p in postings] == ["rp-2"] and "참고" in output
            rows, postings, _ = run(5, 1, role=roles["cloud"])
            assert {p["id"] for p in postings} == {"rp-4", "rp-5", "rp-2"}
            assert all(p["role"] == roles["cloud"] for p in postings)


def collect_and_process():
    """collect 의 제목 선판정(상세 호출 절약)과 process 의 예전 파일 삭제. 목록과 상세 호출은 가짜로 바꾸고 호출 횟수를 센다."""
    import contextlib
    import io
    import json
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    import pipeline.rocketpunch as rp

    def job(job_id, title):
        return {"jobId": job_id, "title": title, "company": {"name": f"회사{job_id}"}, "description": "AWS 운영",
                "webUrl": f"https://example.com/{job_id}"}

    listing = [{"jobId": 1, "title": "클라우드 엔지니어"},      # 제목이 직무 단어에 맞다
               {"jobId": 2, "title": "프론트엔드 개발자"}]      # 안 맞다
    old = rp.RAW_DIR, rp.list_page, rp.load_or_fetch
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        calls: list[str] = []

        def fake_fetch(job_id):  # 실제 호출 대신 횟수만 센다
            calls.append(job_id)
            return job(int(job_id), {"1": "클라우드 엔지니어", "2": "프론트엔드 개발자"}[job_id])

        rp.RAW_DIR, rp.list_page, rp.load_or_fetch = tmp, lambda params: listing, fake_fetch
        base = dict(keyword=None, codes=None, sort="DATE_DESC", size=10, pages=1, n=999,
                    role="클라우드 엔지니어", terms=list(rp.ROLE_TERMS))
        try:
            def run(**kw):
                calls.clear()
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rp.cmd_collect(SimpleNamespace(**{**base, **kw}))
                return out.getvalue()

            # 제목이 안 맞는 공고는 상세를 받지 않는다(호출 0회). 맞는 공고만 받는다
            # 원본 없이 posting 만 남은 예전 통과본(원본을 지웠거나 posting 만 복사해 온 경우)은 건너뛰면서 같이 지운다
            stale = tmp / "rp-2_회사2_예전통과.posting.json"
            stale.write_text("{}", encoding="utf-8")
            out = run(any_title=False)
            assert calls == ["1"], calls
            assert "건너뜀" in out and "호출 1회 절약" in out, out
            assert not stale.exists() and not list(tmp.glob("rp-2*.posting.json")), "건너뛴 공고의 예전 posting 이 남았다"
            # --any-title 이면 제목이 안 맞아도 상세를 받는다(필터는 process 가 한다)
            run(any_title=True)
            assert calls == ["1", "2"], calls
            # 이미 받은 원본이 있으면 호출이 들지 않으니 제목이 안 맞아도 그대로 처리한다
            (tmp / "rp-2.raw.json").write_text("{}", encoding="utf-8")
            run(any_title=False)
            assert calls == ["1", "2"], calls
            (tmp / "rp-2.raw.json").unlink()

            # process: 통과하면 새 이름으로 저장하고, 같은 공고의 예전 이름(옛 규칙, 옛 제목)은 지운다. 번호가 앞부분만 같은 다른 공고는 남긴다
            (tmp / "rp-1.posting.json").write_text("{}", encoding="utf-8")                 # 예전 규칙의 이름
            (tmp / "rp-1_옛회사_옛제목.posting.json").write_text("{}", encoding="utf-8")   # 제목이 바뀌기 전 이름
            (tmp / "rp-10.posting.json").write_text("{}", encoding="utf-8")                # 다른 공고(번호 앞부분이 같음)
            terms = list(rp.ROLE_TERMS)
            with contextlib.redirect_stdout(io.StringIO()):
                assert rp.process(1, "클라우드 엔지니어", terms) is True
            files = sorted(p.name for p in tmp.glob("*.posting.json"))
            assert files == ["rp-10.posting.json", "rp-1_회사1_클라우드-엔지니어.posting.json"], files
            # 필터에서 탈락하면 예전 파일도 남기지 않는다(필터 기준이 바뀌었을 때)
            (tmp / "rp-2_회사2_예전.posting.json").write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                assert rp.process(2, "클라우드 엔지니어", terms) is False
            assert not list(tmp.glob("rp-2*.posting.json"))
        finally:
            rp.RAW_DIR, rp.list_page, rp.load_or_fetch = old


def manual_ids():
    """사이트 검색에서 모은 번호: 중복은 하나만, 검색어·날짜와 함께 기록, fetch --manual 은 아직 안 받은 번호만 받는다."""
    import contextlib
    import io
    import json
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    import pipeline.rocketpunch as rp

    old = rp.RAW_DIR, rp.MANUAL_FILE, rp.MISSING_FILE, rp.load_or_fetch
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        calls: list[str] = []

        def fake_fetch(job_id):
            calls.append(job_id)
            if job_id == "3":  # 로켓펀치에 없는 번호(404)
                raise rp.JobNotFound("HTTP 404 /jobs/3: 공고 정보를 찾을 수 없습니다.")
            raw = {"jobId": int(job_id), "title": "클라우드 엔지니어", "company": {"name": "회사"}, "description": "AWS"}
            (tmp / f"rp-{job_id}.raw.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")  # 받으면 원본이 남는다
            return raw

        rp.RAW_DIR, rp.MANUAL_FILE, rp.MISSING_FILE, rp.load_or_fetch = (
            tmp, tmp / "manual_job_ids.json", tmp / "없는공고.json", fake_fetch)
        try:
            def out_of(fn, **kw):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    fn(SimpleNamespace(**kw))
                return out.getvalue()

            # 입력 안의 중복(3 이 두 번)은 하나만 기록한다
            out = out_of(rp.cmd_add_ids, query="AWS", ids=[1, 2, 3, 3])
            assert rp.manual_ids() == [1, 2, 3] and "새 번호 3개" in out, out
            # 두 번째 검색: 이미 기록한 번호(2, 3)는 빼고 새 번호만 두 번째 묶음에 남긴다. 순서는 처음 모은 순서
            out = out_of(rp.cmd_add_ids, query="클라우드", ids=[3, 2, 4])
            assert rp.manual_ids() == [1, 2, 3, 4] and "새 번호 1개" in out, out
            batches = json.loads(rp.MANUAL_FILE.read_text(encoding="utf-8"))["batches"]
            assert [(b["query"], b["ids"]) for b in batches] == [("AWS", [1, 2, 3]), ("클라우드", [4])]
            assert all(b["collected_at"] and b["source"] for b in batches)  # 검색어·날짜·출처를 남긴다
            # 여러 검색에 나온 번호는 버리지 않고 어느 검색에서 나왔는지 표시한다(예: AWS 와 클라우드 둘 다에 나온 2, 3)
            assert rp.found_by() == {"1": ["AWS"], "2": ["AWS", "클라우드"], "3": ["AWS", "클라우드"], "4": ["클라우드"]}
            # 새 번호가 없으면 묶음을 늘리지 않고 표시만 더한다
            out_of(rp.cmd_add_ids, query="AWS", ids=[1, 4])
            assert len(json.loads(rp.MANUAL_FILE.read_text(encoding="utf-8"))["batches"]) == 2
            assert rp.found_by()["4"] == ["클라우드", "AWS"] and rp.manual_ids() == [1, 2, 3, 4]

            # fetch --manual 은 아직 안 받은 번호만 받는다(이미 받은 2 는 호출 0회)
            (tmp / "rp-2.raw.json").write_text("{}", encoding="utf-8")
            args = dict(ids=[], manual=True, queries=None, role="클라우드 엔지니어", terms=list(rp.ROLE_TERMS))
            out = out_of(rp.cmd_fetch, **args)
            # 3 은 없는 공고(404)라도 멈추지 않고 4 까지 계속 받으며, 3 은 없는 번호로 기록된다
            assert calls == ["1", "3", "4"] and "아직 안 받은 것 3개" in out and "없음\trp-3" in out, (calls, out)
            assert rp._missing_ids() == {3}
            assert (tmp / "rp-1.raw.json").exists() and (tmp / "rp-4.raw.json").exists()
            # 다시 실행하면 받은 번호와 없는 번호(3)를 다시 받으려 하지 않는다(호출 0회)
            calls.clear()
            out = out_of(rp.cmd_fetch, **args)
            assert calls == [] and "아직 안 받은 것 0개" in out and "받을 번호가 없습니다" in out, (calls, out)

            # --queries: 지정한 검색어에서 나온 번호만 받고 나머지는 보류한다(겹친 번호는 지정한 검색에 나왔으면 받는다)
            out_of(rp.cmd_add_ids, query="AI", ids=[4, 8, 9])      # 4 는 클라우드에도 나왔고, 8·9 는 AI 에서만 나왔다
            for n in (8, 9):
                assert str(n) not in {f.stem.split("-")[1].split(".")[0] for f in tmp.glob("rp-*.raw.json")}
            calls.clear()
            out = out_of(rp.cmd_fetch, ids=[], manual=True, queries=["AWS", "클라우드"], role="클라우드 엔지니어",
                         terms=list(rp.ROLE_TERMS))
            assert calls == [] and "--queries 밖이라 보류 2개" in out, (calls, out)   # 8, 9 는 보류, 나머지는 이미 받았다
            out = out_of(rp.cmd_fetch, ids=[], manual=True, queries=["AI"], role="클라우드 엔지니어",
                         terms=list(rp.ROLE_TERMS))
            assert calls == ["8", "9"], calls                                         # AI 만 지정하면 AI 에서 나온 번호만 받는다

            # 404 가 아닌 종료(429 한도 초과 등)는 삼키지 않고 그대로 멈춘다
            def limited(job_id):
                raise SystemExit("호출 한도 초과(429). 5초 뒤에 다시 실행하세요.")

            rp.load_or_fetch = limited
            try:
                out_of(rp.cmd_fetch, ids=[7], manual=False, role="클라우드 엔지니어", terms=list(rp.ROLE_TERMS))
                raise AssertionError("429 에서 멈추지 않았다")
            except SystemExit as stop:
                assert not isinstance(stop, rp.JobNotFound) and "429" in str(stop)
            assert rp._missing_ids() == {3}  # 429 번호는 없는 공고로 기록되지 않는다
        finally:
            rp.RAW_DIR, rp.MANUAL_FILE, rp.MISSING_FILE, rp.load_or_fetch = old


def dedupe_flow():
    """같은 회사·같은 제목의 공고가 번호만 다르게 여러 번 올라오면 가장 최신 게시일의 것만 남긴다."""
    import contextlib
    import csv
    import io
    import json
    import tempfile
    from pathlib import Path

    import pipeline.rocketpunch as rp

    def job(job_id, company, title, start=None, created=None):
        return {"jobId": job_id, "title": title, "company": {"name": company}, "startAt": start, "createdAt": created,
                "description": "본문"}

    raws = [job(10, "X", "AI Engineer", "2026-10-01T00:00:00Z"),
            job(20, "X", "AI Engineer ", "2026-11-01T00:00:00Z"),    # 가장 최신 → 남는다(제목 끝 공백은 같게 본다)
            job(30, "X", "ai engineer", None, "2026-09-01T00:00:00Z"),  # startAt 이 없으면 createdAt 으로 비교
            job(40, "Y", "AI Engineer", "2026-11-05T00:00:00Z"),      # 다른 회사라 묶지 않는다
            job(50, "Z", "", "2026-11-01T00:00:00Z"), job(60, "Z", "", "2026-11-02T00:00:00Z"),  # 제목이 비면 묶지 않는다
            job(70, "W", "DevOps", "2026-11-03T00:00:00Z"), job(80, "W", "DevOps", "2026-11-03T00:00:00Z")]  # 같으면 번호가 큰 것
    assert rp.older_duplicates(raws) == {"10": "20", "30": "20", "70": "80"}

    old = rp.RAW_DIR
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rp.RAW_DIR = tmp
        try:
            for r in raws:
                (tmp / f"rp-{r['jobId']}.raw.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
                (tmp / f"rp-{r['jobId']}_회사_제목.posting.json").write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rp.cmd_dedupe(None)
            left = sorted(p.name.split("_")[0] for p in tmp.glob("*.posting.json"))
            assert left == ["rp-20", "rp-40", "rp-50", "rp-60", "rp-80"], left      # 오래된 posting 만 지워진다
            assert len(list(tmp.glob("rp-*.raw.json"))) == 8                         # 원본은 모두 남는다
            rows = list(csv.DictReader((tmp / "중복공고.csv").open(encoding="utf-8-sig", newline="")))
            assert sorted((r["밀려난 번호"], r["남긴 번호"]) for r in rows) == [("10", "20"), ("30", "20"), ("70", "80")]
            assert "3건" in out.getvalue()
        finally:
            rp.RAW_DIR = old


if __name__ == "__main__":
    demo()
    names()
    export_eval()
    collect_and_process()
    manual_ids()
    dedupe_flow()
    print("통과")
