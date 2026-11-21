"""화면이 부르는 함수 확인(가짜 모드). 실행: BEDROCK_MOCK=1 python -m backend.test_api"""
import os

os.environ["BEDROCK_MOCK"] = "1"

from backend import api  # noqa: E402

JOB = "cloud_engineer"
OWNED = ["Python", "Linux", "Git"]


def demo():
    # 영역별 Gap: 영역의 수요는 그 영역 기술들의 요구 공고 수 합과 같다
    demand = {s["skill"]: s["postings"] for s in api.get_demand(JOB)["skills"]}
    areas = {r["area"]: r for r in api.get_area_gap(JOB, OWNED)}
    assert sum(r["demand"] for r in areas.values()) == sum(demand.values())
    compute = areas["컴퓨팅·서버"]
    assert compute["demand"] == demand["Linux"] + demand["Docker"] + demand["Kubernetes"]
    assert compute["owned"] == demand["Linux"] and compute["missing_skills"] == ["Docker", "Kubernetes"]
    assert "AWS" in areas["공통"]["skills"]                      # 포괄어는 공통
    assert "Python" in areas["배포 자동화(DevOps)"]["skills"]    # 언어는 스크립트·자동화
    assert areas["보안"]["demand"] == 0 and areas["보안"]["coverage"] == 0   # 수요가 없어도 0으로 나눔 없이
    assert api._area_of("Rust") == ("UNCLASSIFIED", "미분류")    # 사전에 없으면 미분류
    assert all("area" in r for r in api.get_gap(JOB, OWNED))

    # 실제 모드 추천: 수요 숫자는 코드가 만들어 넘기고, 과정과 이유는 ai.recommend 가 만든다(여기서는 가짜로 바꿔 확인)
    import ai.recommend as recommend
    calls, old = [], (api.is_mock, api.get_demand, recommend.build_recommendation)
    api.is_mock = lambda: False
    api.get_demand = lambda job: api._load("demand.json")
    recommend.build_recommendation = lambda skill, d: (calls.append((skill, d)) or {"gap_skill": skill, "demand": d, "courses": []}, None)
    try:
        recs = api.get_recommendations(JOB, ["Docker", "Rust"])
    finally:
        api.is_mock, api.get_demand, recommend.build_recommendation = old
    docker = next(s for s in api._load("demand.json")["skills"] if s["skill"] == "Docker")
    total = api._load("demand.json")["total_postings"]
    assert [r["gap_skill"] for r in recs] == ["Docker", "Rust"]
    assert calls[0][1] == {"postings": docker["postings"], "of": total,
                           "required_ratio": round(docker["required"] / docker["postings"], 2)}
    assert calls[1][1] == {"postings": 0, "of": total, "required_ratio": 0}   # 수요에 없는 기술은 0 으로 넘긴다

    # 가짜가 아닌 실제 build_recommendation 이 ③ 형식(courses[] 의 필드와 반환 튜플)을 돌려주는지: 연결이 가짜 모양에만 맞지 않게
    rec, _ = recommend.build_recommendation("Docker", {"postings": 20, "of": 40, "required_ratio": 0.45})
    assert rec["gap_skill"] == "Docker" and rec["courses"]
    assert all(set(c) - {"rating_avg", "rating_count", "duration_min", "language", "aws_services"} == {"title", "url", "level", "prerequisites", "reason", "match"} for c in rec["courses"])
    print("backend.api 확인 통과")


if __name__ == "__main__":
    demo()
