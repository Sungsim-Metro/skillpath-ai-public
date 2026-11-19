"""search_courses 확인. 실행: python -m pipeline.test_search

규칙 단계는 가짜 모드로, 키워드·의미(임베딩) 단계는 순위를 직접 넣어서(실제 Titan 호출 없이) 확인한다.
실제 벡터와 정답 세트로 본 성능은 pipeline/eval_search.py 가 보여 준다.
"""
import json
import os
import tempfile
from pathlib import Path

os.environ["BEDROCK_MOCK"] = "1"  # 이 테스트는 AWS 를 부르지 않는다(.env 의 값이 달라도 가짜 모드로 고정)

from pipeline import search as S  # noqa: E402
from pipeline.build_course_vectors import OUT as VECTORS_FILE  # noqa: E402
from pipeline.course_chunks import all_chunks  # noqa: E402
from pipeline.search import _courses, _links_by_subskill, _mentions, _names, _usable, search_courses  # noqa: E402
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


def hybrid():
    courses = _courses()

    # 키워드 순위: 제목·AWS 서비스에 나온 과정만 후보다. 학습 목표·설명에 스친 것과 일반 단어, 짧은 이름은 세우지 않는다
    assert "AK479CRG5K" in S._keyword_ranking("PostgreSQL")  # 제목에 PostgreSQL
    for skill in ("Azure", "GCP", "React", "Go", "Python"):
        assert S._keyword_ranking(skill) == [], skill

    # 순위 합치기(RRF): 두 순위에 모두 있는 과정이 위로 온다
    a, b, c = "6MCV5DBF5N", "TXYJ7YH7EU", "TYAAKGU914"
    assert S._fuse([a, b], [b, c])[0] == b
    assert S._fuse([a], [a]) == [a] and S._fuse() == []

    # 방식 이름 검사, 가짜 모드에서는 벡터를 쓸 수 없다
    try:
        search_courses("Docker", mode="nope")
        raise AssertionError("없는 mode 는 ValueError")
    except ValueError:
        pass
    assert not S._semantic_ready()
    try:
        search_courses("Network", mode="embedding")
        raise AssertionError("벡터를 못 쓰면 RuntimeError")
    except RuntimeError:
        pass
    assert search_courses("PostgreSQL", mode="keyword")[0]["course_id"] in S._keyword_ranking("PostgreSQL")  # 키워드만은 벡터 없이도 돈다
    assert search_courses("Network") == search_courses("Network", mode="rules") == []  # 가짜 모드의 final 은 규칙 결과 그대로
    assert search_courses("   ") == []

    # 벡터를 쓸 수 있다고 하고 순위를 직접 넣는다
    old = S._semantic_ready, S._keyword_ranking, S._semantic_ranking
    try:
        S._semantic_ready = lambda: True
        S._keyword_ranking = lambda skill: [a, b]
        S._semantic_ranking = lambda skill: [b, c]
        net = search_courses("Network")  # 규칙으로는 비어 있어서 섞은 순위로 채운다
        assert [f["course_id"] for f in net] == [b, a, c]
        assert all(f["match"] == "uncertain" and f["subskill_id"] is None for f in net)  # 규칙으로 찾은 연결이 아니다
        assert len(search_courses("Network", k=2)) == 2 and all(f["course_id"] in courses for f in net)
        # 규칙 결과가 먼저이고 certain 은 그대로다. 같은 과정은 두 번 나오지 않는다
        docker = search_courses("Docker")
        assert docker == search_courses("Docker", mode="rules")  # 이미 3개라 채우지 않는다
        k8s = search_courses("Kubernetes", k=5)
        assert k8s[: len(search_courses("Kubernetes", mode="rules", k=5))] == search_courses("Kubernetes", mode="rules", k=5)
        assert len({f["course_id"] for f in k8s}) == len(k8s)
        # 채우는 단계가 실패하면(Titan 호출 오류 등) 오류로 멈추지 않고 경고를 남긴 뒤 규칙 결과를 준다. 방식을 직접 고르면 오류를 그대로 낸다
        def boom(skill):
            raise ConnectionError("Titan down")

        S._semantic_ranking = boom
        import logging

        class Capture(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records = []

            def emit(self, record):
                self.records.append(record.getMessage())

        cap = Capture()
        logging.getLogger("pipeline.search").addHandler(cap)
        try:
            assert search_courses("Network") == search_courses("Network", mode="rules") == []
            assert len(cap.records) == 1 and "ConnectionError" in cap.records[0], cap.records
            try:
                search_courses("Network", mode="embedding")
                raise AssertionError("방식을 직접 고르면 오류를 그대로 낸다")
            except ConnectionError:
                pass
        finally:
            logging.getLogger("pipeline.search").removeHandler(cap)
        S._semantic_ranking = lambda skill: [b, c]
        # 사람이 지정한 입문 과정(AWS)은 채우지 않는다
        assert search_courses("AWS") == [{"course_id": "94T2BEN85A", "subskill_id": None, "match": "uncertain"}]
        # 단독 방식
        assert [f["course_id"] for f in search_courses("Network", mode="keyword")] == [a, b]
        assert [f["course_id"] for f in search_courses("Network", mode="embedding")] == [b, c]
    finally:
        S._semantic_ready, S._keyword_ranking, S._semantic_ranking = old

    # 벡터 파일: 모델이 다르면 쓰지 않는다
    old_file, old_model = S.VECTORS, os.environ.get("EMBED_MODEL_ID")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "v.json"
        f.write_text(json.dumps({"model": "m-1", "dim": 2, "items": [{"course_id": a, "field": "title", "vector": [1, 0]}]}))
        try:
            S.VECTORS = f
            os.environ["EMBED_MODEL_ID"] = "m-2"
            S._vectors.cache_clear()
            assert S._vectors() is None
            os.environ["EMBED_MODEL_ID"] = "m-1"
            S._vectors.cache_clear()
            assert S._vectors() == {a: [[1, 0]]}
        finally:
            S.VECTORS = old_file
            if old_model is None:
                os.environ.pop("EMBED_MODEL_ID", None)
            else:
                os.environ["EMBED_MODEL_ID"] = old_model
            S._vectors.cache_clear()

    # 저장소에 올린 벡터 파일이 현재 과정 데이터와 맞는다(과정을 바꿨는데 벡터를 다시 안 만들었을 때 알아챈다)
    data = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
    chunks = all_chunks()
    assert [(i["course_id"], i["field"]) for i in data["items"]] == [(c["course_id"], c["field"]) for c in chunks]
    assert {len(i["vector"]) for i in data["items"]} == {data["dim"]}
    assert all(_usable(courses[i["course_id"]]) for i in data["items"])


def jobs():
    """AI 세부 능력 검색어(2026-11-19): 클라우드와 별칭이 겹치지 않고, AI 기술은 A* 로, 클라우드 진단은 C* 만 본다."""
    from pipeline.aliases import _key

    by_job = {job: {_key(a) for r in S._read(path) for a in r["aliases"].split("|") if a.strip()}
              for job, path in S.QUERY_FILES.items()}
    overlap = by_job["cloud_engineer"] & by_job["ai_engineer"]
    assert not overlap, f"클라우드와 AI 검색어 별칭이 겹친다(겹치면 클라우드 추천이 바뀐다): {overlap}"
    assert S.subskills_of("RAG") == ["A1-2"], S.subskills_of("RAG")
    assert S.subskills_of("SageMaker") == ["A2-3"], S.subskills_of("SageMaker")
    assert S.subskills_of("Python", "cloud_engineer") == [], S.subskills_of("Python", "cloud_engineer")
    assert S.subskills_of("Docker") == S.subskills_of("Docker", "cloud_engineer") == ["C1-4"]
    print("직무별 검색어: 별칭 겹침 없음, AI 기술 → A* 연결 확인")


if __name__ == "__main__":
    demo()
    hybrid()
    jobs()
    print("통과")
