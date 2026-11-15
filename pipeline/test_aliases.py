"""별칭 사전 확인. 실행: python -m pipeline.test_aliases"""
import json
from pathlib import Path

from pipeline.aliases import _index, normalize_skill, normalize_skills

GOLD = Path(__file__).resolve().parent.parent / "ai" / "eval" / "gold_extraction.json"


def demo():
    _index()  # 별칭 충돌이 있으면 여기서 오류
    # 표기가 달라도 같은 기술
    for raw in ("k8s", "K8S", "쿠버네티스", "Kubernetes", "쿠버네티스(EKS)"):
        assert normalize_skill(raw) == "Kubernetes", raw
    assert normalize_skill("CICD") == normalize_skill("CI/CD") == "CI/CD"
    assert normalize_skill("aws") == normalize_skill("아마존 웹 서비스") == "AWS"
    # 비슷해도 합치지 않는다
    assert normalize_skill("JavaScript") is None
    assert normalize_skill("EKS") is None
    assert normalize_skill("IaC") is None
    assert normalize_skill("") is None
    # 목록: 순서 유지, 중복 제거, 없는 표현은 따로
    assert normalize_skills(["도커", "Docker", "Rust", "파이썬", "Rust"]) == (["Docker", "Python"], ["Rust"])
    # 성진의 정답지: 모든 raw 가 정답지의 normalized 로 합쳐져야 한다
    for posting in json.loads(GOLD.read_text(encoding="utf-8")):
        for s in posting["skills"]:
            assert normalize_skill(s["raw"]) == s["normalized"], (s["raw"], normalize_skill(s["raw"]), s["normalized"])


if __name__ == "__main__":
    demo()
    print("통과")
