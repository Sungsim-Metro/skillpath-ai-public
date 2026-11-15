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
    assert p["text"] == "AWS 운영\nPython 개발 경험\nLinux 이해\nDocker 우대", p["text"]
    assert not re.search(r"<[^>]+>|&\w+;", p["text"])
    assert matched_terms(p) == ["aws"]
    assert matched_terms(p, ["aws", "클라우드"]) == ["aws", "클라우드"]  # 제목의 '클라우드'
    assert matched_terms(p, ["kafka"]) == []
    assert matched_terms({"title": "laws", "text": "flaws, AWS2"}, ["aws"]) == []  # 단어 경계


if __name__ == "__main__":
    demo()
    print("통과")
