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

    # 시장 Gap: 비중은 각각 합이 100, Gap 은 그 차이
    market = api.get_market_gap(JOB)
    from ai.sections import SECTION_WEIGHT, TYPE_WEIGHT
    assert market["weights"] == {"section": SECTION_WEIGHT, "type": TYPE_WEIGHT, "applied": "type"}  # 가중치 표는 한 곳
    rows = {r["skill"]: r for r in market["rows"]}
    assert abs(sum(r["demand_pct"] for r in rows.values()) - 100) < 1
    assert abs(sum(r["supply_pct"] for r in rows.values()) - 100) < 1
    for r in rows.values():
        assert abs(r["gap_pp"] - (r["demand_pct"] - r["supply_pct"])) <= 0.11, r
    assert rows["AWS"]["gap_pp"] > 0        # 공고는 많이 원하는데 교과목은 적다
    assert rows["Python"]["gap_pp"] < 0     # 수요가 많아도 교과목이 더 많이 다루면 -
    assert rows["SQL"]["demand_pct"] == 0   # 공급에만 있는 기술도 표에 나온다
    assert [r["gap_pp"] for r in market["rows"]] == sorted((r["gap_pp"] for r in market["rows"]), reverse=True)
    print("backend.api 확인 통과")


if __name__ == "__main__":
    demo()
