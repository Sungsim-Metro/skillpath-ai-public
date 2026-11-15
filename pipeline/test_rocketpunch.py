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
    """채점용 내보내기: 튜닝에 쓴 공고는 빼고, AWS 언급 공고를 먼저(직무 통과 우선), 나머지는 직무 통과 공고로 채운다."""
    import contextlib
    import csv
    import io
    import json
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    import pipeline.rocketpunch as rp

    def raw(job_id, title, body):
        return {"jobId": job_id, "title": title, "company": {"name": f"회사{job_id}"}, "description": body,
                "webUrl": f"https://example.com/{job_id}"}

    jobs = [raw(1, "클라우드 엔지니어", "AWS 운영"),            # 튜닝에 쓴 공고라 빠진다
            raw(2, "클라우드 엔지니어", "AWS 운영 경험"),       # 직무 통과 + AWS
            raw(3, "DevOps Engineer", "쿠버네티스 운영"),        # 직무 통과
            raw(4, "백엔드 개발자", "AWS 사용 경험"),            # 직무 밖 + AWS
            raw(5, "프론트엔드 개발자", "React 개발"),           # 직무 밖, AWS 없음 → 안 뽑힌다
            raw(6, "SRE", "모니터링 운영")]                      # 직무 통과
    old = rp.RAW_DIR, rp.EVAL_DIR, rp.TUNING_FILE
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rp.RAW_DIR, rp.EVAL_DIR, rp.TUNING_FILE = tmp, tmp / "eval", tmp / "tuning_ids.json"
        try:
            rp.TUNING_FILE.write_text(json.dumps({"ids": [1]}), encoding="utf-8")
            for j in jobs:
                (tmp / f"rp-{j['jobId']}.raw.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
            (tmp / "eval").mkdir()
            (tmp / "eval" / "eval-99_예전.json").write_text("{}", encoding="utf-8")  # 다시 내보내면 지워진다
            args = SimpleNamespace(n=4, min_aws=2, role="클라우드 엔지니어", terms=list(rp.ROLE_TERMS))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rp.cmd_export_eval(args)
            files = sorted(p.name for p in (tmp / "eval").glob("eval-*.json"))
            assert len(files) == 4 and not any("99" in f for f in files), files
            rows = list(csv.DictReader((tmp / "eval" / "eval-목록.csv").open(encoding="utf-8-sig", newline="")))
            ids = [r["id"] for r in rows]
            assert ids[:2] == ["rp-2", "rp-4"], ids          # AWS 언급 공고 2건이 먼저(직무 통과가 앞)
            assert set(ids) == {"rp-2", "rp-4", "rp-6", "rp-3"}  # 튜닝에 쓴 1번, AWS 없는 직무 밖 5번은 없다
            assert [r["직무 필터"] for r in rows[:2]] == ["통과", "밖"] and rows[1]["AWS 언급"] == "있음"
            # 직무 필터 밖 공고는 role 을 바꿔 수요 집계에 섞이지 않게 표시한다
            out_file = next(p for p in (tmp / "eval").glob("eval-*_rp-4_*.json"))
            assert json.loads(out_file.read_text(encoding="utf-8"))["role"] == rp.NOT_ROLE
            # 목표에 못 미치면 경고를 낸다
            args.n, args.min_aws = 10, 4
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rp.cmd_export_eval(args)
            assert "목표" in out.getvalue() and "부족" in out.getvalue()

            # --min-aws 가 --n 보다 커도 n 건을 넘지 않고 엉뚱하게 잘리지 않는다(음수 슬라이스 방지)
            for old_raw in tmp.glob("rp-*.raw.json"):
                old_raw.unlink()
            many = [raw(11, "클라우드 엔지니어", "AWS 운영"), raw(12, "인프라 엔지니어", "AWS 운영"),
                    raw(13, "DevOps Engineer", "AWS 운영"), raw(14, "백엔드 개발자", "AWS 사용"),
                    raw(15, "풀스택 개발자", "AWS 사용"), raw(16, "SRE", "모니터링")]
            for j in many:
                (tmp / f"rp-{j['jobId']}.raw.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
            args.n, args.min_aws = 3, 5
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rp.cmd_export_eval(args)
            rows = list(csv.DictReader((tmp / "eval" / "eval-목록.csv").open(encoding="utf-8-sig", newline="")))
            assert len(rows) == 3 and len(list((tmp / "eval").glob("eval-*.json"))) == 3, rows
            assert [r["id"] for r in rows] == ["rp-13", "rp-12", "rp-11"]  # AWS 언급 + 직무 통과가 먼저, 최신 번호 순
            assert all(r["AWS 언급"] == "있음" for r in rows) and "참고" in out.getvalue()
            args.n, args.min_aws = 1, 99
            with contextlib.redirect_stdout(io.StringIO()):
                rp.cmd_export_eval(args)
            assert len(list((tmp / "eval").glob("eval-*.json"))) == 1
        finally:
            rp.RAW_DIR, rp.EVAL_DIR, rp.TUNING_FILE = old


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


if __name__ == "__main__":
    demo()
    names()
    export_eval()
    collect_and_process()
    print("통과")
