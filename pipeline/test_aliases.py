"""별칭 사전 확인. 실행: python -m pipeline.test_aliases"""
import json
from pathlib import Path

from pipeline.aliases import _index, normalize_skill, normalize_skills

GOLD = Path(__file__).resolve().parent.parent / "ai" / "eval" / "gold_extraction.json"


def demo():
    _index()  # 별칭 충돌이 있으면 여기서 오류
    # 표기가 달라도 같은 기술
    for raw in ("k8s", "K8S", "쿠버네티스", "Kubernetes"):
        assert normalize_skill(raw) == "Kubernetes", raw
    # EKS 는 AWS 서비스라 Kubernetes 와 따로 둔다(성진님 합의). 둘이 함께 적힌 표현은 합치지 않고 사전 밖으로 둔다
    for raw in ("EKS", "Amazon EKS", "Elastic Kubernetes Service"):
        assert normalize_skill(raw) == "EKS", raw
    assert normalize_skill("Kubernetes(EKS)") is None and normalize_skill("쿠버네티스(EKS)") is None
    # 실제 공고 11건의 추출에서 사전에 없던 도구 이름(개념어 클라우드·DevOps·메시지 큐는 넣지 않았다)
    for raw, canon in (("Istio", "Istio"), ("앤서블", "Ansible"), ("카프카", "Kafka"), ("Apache Hadoop", "Hadoop"),
                       ("Hyper-V", "Hyper-V"), ("HyperV", "Hyper-V"), ("pfsense", "pfSense"), ("시스코", "Cisco"),
                       ("Amazon API Gateway", "API Gateway"), ("Infrastructure as Code", "IaC")):
        assert normalize_skill(raw) == canon, raw
    # 성진님 사전 후보 34개 중 클라우드·인프라·데이터 도구. 웹 프론트엔드 용어·AI 도구 제품명·Argo 는 보류라 아직 없다
    for raw, canon in (("ECS", "ECS"), ("Amazon ECS", "ECS"), ("amazon cloudfront", "CloudFront"),
                       ("Amazon Redshift", "Redshift"), ("Google BigQuery", "BigQuery"), ("Atlantis", "atlantis"),
                       ("Windows", "Windows"), ("Xen", "Xen")):
        assert normalize_skill(raw) == canon, raw
    for held in ("Argo", "Claude API", "GitHub Copilot", "CSS", "jQuery", "웹팩"):
        assert normalize_skill(held) is None, held
    for concept in ("클라우드", "DevOps", "메시지 큐"):
        assert normalize_skill(concept) is None, concept
    assert normalize_skill("IaC") == "IaC" != normalize_skill("Terraform")  # IaC 는 Terraform 과 합치지 않는다
    assert normalize_skill("CICD") == normalize_skill("CI/CD") == "CI/CD"
    assert normalize_skill("aws") == normalize_skill("아마존 웹 서비스") == "AWS"
    # 비슷해도 합치지 않는다
    assert normalize_skill("JavaScript") is None
    assert normalize_skill("") is None
    # 목록: 순서 유지, 중복 제거, 없는 표현은 따로
    assert normalize_skills(["도커", "Docker", "Rust", "파이썬", "Rust"]) == (["Docker", "Python"], ["Rust"])
    # 성진의 정답지: 사전이 아는 raw 는 정답지의 normalized 와 같아야 한다(틀리게 합치지 않는다).
    # 사전이 모르는 raw(TCP/IP, 정답지가 EKS 분리 전의 쿠버네티스(EKS) 등)는 막지 않고 신규 후보로 모아 보여 준다.
    unresolved = []
    for posting in json.loads(GOLD.read_text(encoding="utf-8")):
        for s in posting["skills"]:
            got = normalize_skill(s["raw"])
            if got is None:
                unresolved.append(s["raw"])
            else:
                assert got == s["normalized"], (s["raw"], got, s["normalized"])
    print(f"정답지 중 사전 밖 raw {len(unresolved)}개(신규 후보): {sorted(set(unresolved))}")


if __name__ == "__main__":
    demo()
    print("통과")
