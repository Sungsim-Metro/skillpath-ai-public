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
    for skill in ("Azure", "GCP", "JavaScript", "Network", ""):
        assert search_courses(skill) == [], skill

    # AWS 는 넓어서 세부 능력으로 고를 수 없으므로 지정한 입문 과정을 준다(subskill_id 는 None)
    assert search_courses("AWS") == [{"course_id": "94T2BEN85A", "subskill_id": None}]
    assert search_courses("아마존 웹 서비스") == search_courses("AWS")  # 별칭도 같은 결과
    # 지정한 과정은 실제 데이터에 있고, 기초 난이도이며, 종료 예정이 아니고, 학습 목표가 있어야 한다
    import csv
    from pathlib import Path
    rows = {r["course_id"]: r for r in csv.DictReader(
        (Path(__file__).resolve().parent.parent / "data" / "courses" / "courses.csv").open(encoding="utf-8-sig", newline=""))}
    for f in search_courses("AWS"):
        c = rows[f["course_id"]]
        assert c["level"] == "기초" and not c["retiring"].strip() and c["objectives"].strip(), c["title"]


if __name__ == "__main__":
    demo()
    print("통과")
