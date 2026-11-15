"""to_posting 확인. 실행: python -m pipeline.test_rocketpunch"""
import re

from pipeline.rocketpunch import matched_terms, to_posting

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


if __name__ == "__main__":
    demo()
    print("통과")
