"""search_courses 확인. 실행: python -m pipeline.test_search"""
from pipeline.search import _courses, _links_by_subskill, _mentions, _names, _usable, search_courses
from pipeline.aliases import normalize_skill


def demo():
    courses = _courses()
    links = {(r["course_id"], sid) for sid, rows in _links_by_subskill().items() for r in rows}

    skills = ("Docker", "Kubernetes", "EKS", "ECS", "EC2", "Terraform", "IaC", "CI/CD", "IAM", "S3", "Linux", "Git")
    for skill in skills:
        found = search_courses(skill)
        assert 0 < len(found) <= 3, (skill, found)
        assert len({f["course_id"] for f in found}) == len(found)  # 같은 과정이 두 번 나오지 않는다
        names = _names(normalize_skill(skill) or skill)
        for f in found:
            c = courses[f["course_id"]]
            assert f["match"] in ("certain", "uncertain") and _usable(c), f  # 종료 예정·학습 목표 없는 과정은 뺀다
            if f["subskill_id"] is not None:  # 연결은 실제 데이터에 있어야 한다(부르는 쪽이 행을 조회한다)
                assert (f["course_id"], f["subskill_id"]) in links, f
            if f["match"] == "certain":  # certain 은 이름이 제목·학습 목표에 있다는 뜻이다(틀린 확신을 주지 않는다)
                assert _mentions(c["title"], names) or _mentions(c["objectives"], names), (skill, c["title"])
        # certain 이 앞, 그 안에서는 기초가 중급보다 앞
        order = [f["match"] != "certain" for f in found]
        assert order == sorted(order), (skill, found)

    # Kubernetes: 쿠버네티스를 다루는 과정이 certain 으로 먼저 나오고, 컨테이너 기초는 certain 이 아니다
    k8s = search_courses("Kubernetes")
    assert k8s[0] == {"course_id": "6MCV5DBF5N", "subskill_id": "C7-2", "match": "certain"}  # 기초, 제목에 Kubernetes
    assert all(f["match"] == "uncertain" for f in k8s if courses[f["course_id"]]["title"].startswith("Introduction to Container"))
    # EKS 는 Kubernetes 와 같은 과정을 받을 수 있지만 EKS 과정이 certain 이다
    assert {"6MCV5DBF5N"} <= {f["course_id"] for f in search_courses("EKS") if f["match"] == "certain"}
    # Docker: 이름이 학습 목표에 있는 과정 하나만 certain, 나머지 컨테이너 기초는 uncertain
    docker = search_courses("Docker")
    assert [f["match"] for f in docker] == ["certain", "uncertain", "uncertain"]
    assert all(f["subskill_id"] == "C1-4" for f in docker)
    # Terraform: 과정 본문에 Terraform 이 없으면 CloudFormation 과정이 나오되 certain 이 아니다
    assert all(f["match"] == "uncertain" for f in search_courses("Terraform"))

    assert search_courses("쿠버네티스") == search_courses("Kubernetes")  # 별칭도 같은 결과
    assert len(search_courses("Docker", k=1)) == 1
    # 맞는 세부 능력이 없으면 빈 목록(그럴듯하게 채우지 않는다)
    for skill in ("Azure", "GCP", "JavaScript", "Network", "Python", ""):
        assert search_courses(skill) == [], skill

    # AWS 는 넓어서 세부 능력으로 고를 수 없으므로 지정한 입문 과정을 준다(subskill_id 는 None, match 는 uncertain)
    assert search_courses("AWS") == [{"course_id": "94T2BEN85A", "subskill_id": None, "match": "uncertain"}]
    assert search_courses("아마존 웹 서비스") == search_courses("AWS")  # 별칭도 같은 결과
    # 지정한 과정은 실제 데이터에 있고, 기초 난이도이며, 종료 예정이 아니고, 학습 목표가 있어야 한다
    for f in search_courses("AWS"):
        c = courses[f["course_id"]]
        assert c["level"] == "기초" and _usable(c), c["title"]


if __name__ == "__main__":
    demo()
    print("통과")
