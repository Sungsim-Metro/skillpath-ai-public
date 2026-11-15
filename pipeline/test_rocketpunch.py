"""to_posting 확인. 실행: python -m pipeline.test_rocketpunch"""
import re

from pipeline.rocketpunch import matched_terms, to_posting

RAW = {
    "jobId": 1,
    "title": "클라우드 &amp; 백엔드 엔지니어",
    "company": {"name": "가상회사"},
    "description": "<p>AWS 운영</p><p>&nbsp;</p>",
    "duty": None,
    "minimumQualification": "Python 개발 경험<br/>Linux 이해",
    "preferredQualification": "Docker 우대",
    "benefit": "",
}


def demo():
    p = to_posting(RAW)
    assert set(p) == {"posting_id", "title", "company", "job_category", "text", "source", "collected_at"}
    assert p["posting_id"] == "rp-1" and p["source"] == "rocketpunch"
    assert p["title"] == "클라우드 & 백엔드 엔지니어"
    assert p["text"] == "AWS 운영\nPython 개발 경험\nLinux 이해\nDocker 우대", p["text"]
    assert not re.search(r"<[^>]+>|&\w+;", p["text"])
    assert matched_terms(p) == ["aws"]
    assert matched_terms(p, ["aws", "클라우드"]) == ["aws", "클라우드"]  # 제목의 '클라우드'
    assert matched_terms(p, ["kafka"]) == []
    assert matched_terms({"title": "laws", "text": "flaws, AWS2"}, ["aws"]) == []  # 단어 경계


if __name__ == "__main__":
    demo()
    print("통과")
