"""NCS 매핑 확인. 실행: python -m pipeline.test_ncs"""
import csv
import json
from pathlib import Path

from pipeline.ncs import _links, _units, ncs_name, ncs_unit

ROOT = Path(__file__).resolve().parent.parent


def demo():
    queries = {r["subskill_id"]: r for r in csv.DictReader(
        (ROOT / "pipeline" / "courses" / "queries.csv").open(encoding="utf-8-sig", newline=""))}
    links, units = _links(), _units()

    assert len(units) == 30  # 분류표에서 뽑은 클라우드 단위
    assert set(links) == set(queries), "세부 능력 31개가 모두 연결표에 있어야 한다(없으면 unit=null 로 적는다)"

    for sid, link in links.items():
        if link["unit"] is None:
            assert link["confidence"] is None, sid
            continue
        assert link["confidence"] in ("certain", "uncertain"), sid
        assert link["unit"] in units, (sid, link["unit"])
        # 연결한 단위의 영역에 그 세부 능력의 영역이 들어 있어야 한다(분류표의 배정과 모순 금지)
        assert queries[sid]["area_id"] in units[link["unit"]]["areas"], (sid, link["unit"])

    # 확신이 있는 연결만 값이 되고, 나머지는 None
    for skill, code in (("EC2", "2001021506"), ("S3", "2001021507"), ("IAM", "2001061207"),
                        ("CI/CD", "2001021510"), ("VPC", "2001021508")):
        assert ncs_unit(skill) == code, skill
    assert ncs_unit("쿠버네티스") is None and ncs_unit("Docker") is None   # 컨테이너: NCS 에 단위 없음
    assert ncs_unit("Terraform") is None                                    # IaC: NCS 에 0건
    assert ncs_unit("Linux") is None                                        # uncertain 연결은 쓰지 않는다
    assert ncs_unit("Network") is None                                      # 세부 능력이 둘이라 정할 수 없다
    assert ncs_unit("AWS") is None and ncs_unit("JavaScript") is None and ncs_unit("") is None

    assert ncs_name("2001021506") == "클라우드 서버 서비스 개발" and ncs_name("0000000000") is None


if __name__ == "__main__":
    demo()
    print("통과")
