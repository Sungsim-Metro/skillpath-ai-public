"""search_courses 확인. 실행: python -m pipeline.test_search

규칙과 KB 검색 응답을 AWS 호출 없이 확인한다. 실제 KB와 정답 세트 평가는 pipeline/eval_search.py를 사용한다.
"""
import json
import os
from unittest.mock import Mock, patch

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
        assert docker[0] == search_courses("Docker", mode="rules")[0]
        assert all(row["match"] == "uncertain" for row in docker[1:])
        k8s = search_courses("Kubernetes", k=5)
        certain = [r for r in search_courses("Kubernetes", mode="rules", k=5) if r["match"] == "certain"]
        assert all(r["match"] == "certain" for r in k8s[:len(certain)])
        assert len({f["course_id"] for f in k8s}) == len(k8s)
        # KB 운영 오류는 정상적인 빈 검색 결과와 구분되어야 재시도가 가능하다
        def boom(skill):
            raise ConnectionError("KB unavailable")

        S._semantic_ranking = boom
        for mode in ("final", "embedding"):
            try:
                search_courses("Network", mode=mode)
                raise AssertionError("KB 오류가 호출자에게 전달되어야 한다")
            except ConnectionError:
                pass
        S._semantic_ranking = lambda skill: [b, c]
        # 사람이 지정한 입문 과정(AWS)은 채우지 않는다
        with patch.object(S, "_kb_scores", return_value={"94T2BEN85A": 0.1}):
            assert search_courses("AWS") == [{"course_id": "94T2BEN85A", "subskill_id": None, "match": "uncertain"}]
        # 단독 방식
        assert [f["course_id"] for f in search_courses("Network", mode="keyword")] == [a, b]
        assert [f["course_id"] for f in search_courses("Network", mode="embedding")] == [b, c]
    finally:
        S._semantic_ready, S._keyword_ranking, S._semantic_ranking = old

    # 저장소에 올린 벡터 파일이 현재 과정 데이터와 맞는다(과정을 바꿨는데 벡터를 다시 안 만들었을 때 알아챈다)
    data = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
    chunks = all_chunks()
    assert [(i["course_id"], i["field"]) for i in data["items"]] == [(c["course_id"], c["field"]) for c in chunks]
    assert {len(i["vector"]) for i in data["items"]} == {data["dim"]}
    assert all(_usable(courses[i["course_id"]]) for i in data["items"])


def test_kb_retrieval():
    a, b = "6MCV5DBF5N", "TXYJ7YH7EU"
    unusable = next(cid for cid, course in _courses().items() if not _usable(course))
    hits = [(a, 0.83), (b, 0.91), (a, 0.97), ("unknown", 1.0), (unusable, 1.0)]
    client = Mock()
    client.retrieve.return_value = {
        "retrievalResults": [{"score": score, "metadata": {"course_id": cid}} for cid, score in hits]
        + [{"score": 1, "metadata": {}}, {"score": 0.77, "metadata": {"course_id": "TYAAKGU914"}}]
    }
    with patch.dict(os.environ, {"BEDROCK_MOCK": "0", "BEDROCK_KB_ID": "EXAMPLE1234"}), \
         patch.object(S, "_kb_client", return_value=client):
        assert [r["course_id"] for r in search_courses("Docker", mode="embedding")] == [a, b]
        args = client.retrieve.call_args.kwargs
        assert args["knowledgeBaseId"] == "EXAMPLE1234" and "Docker" in args["retrievalQuery"]["text"]
        filters = args["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]["andAll"]
        assert {"equals": {"key": "type", "value": "program"}} in filters
        assert {"equals": {"key": "recommendable", "value": True}} in filters
        client.retrieve.reset_mock()
        assert search_courses("Docker", k=1)[0]["match"] == "certain"
        client.retrieve.assert_called_once()
        client.retrieve.return_value = {"retrievalResults": [{"score": 0.99, "metadata": {"course_id": "TYAAKGU914"}}]}
        assert search_courses("Kubernetes", k=1)[0]["course_id"] == "TYAAKGU914"
        assert search_courses("Kubernetes", k=1, mode="rules")[0]["course_id"] == a
        client.retrieve.return_value = {"retrievalResults": [
            {"score": 0.1, "metadata": {"course_id": "94T2BEN85A"}},
            {"score": 1, "metadata": {"course_id": a}},
        ]}
        assert search_courses("AWS") == [{"course_id": "94T2BEN85A", "subskill_id": None, "match": "uncertain"}]
        config = client.retrieve.call_args.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert {"in": {"key": "course_id", "value": ["94T2BEN85A"]}} in config["filter"]["andAll"]
        client.retrieve.return_value = {"retrievalResults": []}
        assert search_courses("AWS") == []
        assert search_courses("Network", mode="embedding") == []
        with patch.dict(os.environ, {"BEDROCK_KB_ID": ""}):
            try:
                search_courses("Docker", k=1)
                raise AssertionError("실제 모드에서는 KB 설정이 필요하다")
            except RuntimeError as error:
                assert "BEDROCK_KB_ID" in str(error)
        with patch.dict(os.environ, {"BEDROCK_MOCK": "1"}):
            client.retrieve.reset_mock()
            assert search_courses("Docker") == search_courses("Docker", mode="rules")
            client.retrieve.assert_not_called()
def bm25():
    """BM25 단계(비교용 방식). 낱말 쪼개기, 순위, 임계값, 기본 방식이 바뀌지 않았는지 확인한다."""
    assert S._tokens("Run EC2. Use S3-backed, CI/CD") == ["run", "ec2", "use", "s3-backed", "ci", "cd"]
    courses = _courses()
    ranked = S._bm25_ranking("EKS")
    assert ranked and all(_usable(courses[c]) for c in ranked)
    assert "eks" in courses[ranked[0]]["title"].lower() or "eks" in courses[ranked[0]]["aws_services"].lower()  # 이름이 제목·서비스에 나오는 과정이 1등
    assert S._bm25_ranking("zzzunknownskill") == []
    assert all(v > S.BM25_MIN for v in (S._bm25_scores("EKS")[c] for c in ranked))  # 임계값 이하는 뺀다
    assert {"bm25", "hybrid_bm25", "final_bm25"} <= set(S.MODES)
    assert search_courses("Kubernetes", 3, mode="bm25")  # 가짜 모드에서도 키워드 방식이므로 돈다
    try:
        search_courses("Kubernetes", 3, mode="hybrid_bm25")
        raise AssertionError("가짜 모드에서 의미 검색은 막혀야 한다")
    except RuntimeError:
        pass
    assert search_courses("EKS", 3) == search_courses("EKS", 3, mode="final")  # 기본 방식은 그대로 final
    assert search_courses("EKS", 3, mode="final_bm25") == search_courses("EKS", 3, mode="final")  # 가짜 모드의 final 계열은 규칙만 쓴다


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
    test_kb_retrieval()
    bm25()
    jobs()
    print("통과")
