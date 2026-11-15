"""search_courses 확인. 실행: python -m pipeline.test_search"""
from pipeline.search import _course_text, _links_by_subskill, search_courses


def demo():
    courses = _course_text()
    links = {(r["course_id"], sid) for sid, rows in _links_by_subskill().items() for r in rows}

    for skill, subskill in (("Docker", "C1-4"), ("Kubernetes", "C1-4"), ("EC2", "C1-1"), ("Terraform", "C5-4"),
                            ("CI/CD", "C5-1"), ("IAM", "C4-1"), ("S3", "C2-1")):
        found = search_courses(skill)
        assert 0 < len(found) <= 3, (skill, found)
        assert all(f["subskill_id"] == subskill for f in found), (skill, found)
        assert len({f["course_id"] for f in found}) == len(found)  # 같은 과정이 두 번 나오지 않는다
        for f in found:  # 과정과 연결이 실제 데이터에 있어야 한다
            assert f["course_id"] in courses and (f["course_id"], f["subskill_id"]) in links, f

    assert search_courses("쿠버네티스") == search_courses("Kubernetes")  # 별칭도 같은 결과
    assert len(search_courses("Docker", k=1)) == 1
    # 맞는 세부 능력이 없으면 빈 목록(그럴듯하게 채우지 않는다)
    for skill in ("AWS", "Azure", "GCP", "JavaScript", "Network", ""):
        assert search_courses(skill) == [], skill


if __name__ == "__main__":
    demo()
    print("통과")
