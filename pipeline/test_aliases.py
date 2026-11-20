"""별칭 사전 확인. 실행: python -m pipeline.test_aliases"""
import json
import unittest
from pathlib import Path

from pipeline.aliases import _index, find_skills, normalize_skill, normalize_skills

GOLD = Path(__file__).resolve().parent.parent / "ai" / "eval" / "gold_extraction.json"


class FindSkillsTest(unittest.TestCase):
    def test_aliases_in_prose_have_word_boundaries_and_allow_korean_particles(self):
        text = "자바스크립트와 Dockerfile, NoSQL, nextXjs. 도커에서는 docker를, 자바와 K8S를 사용합니다."
        self.assertEqual(find_skills(text), ["Docker", "Java", "JavaScript", "Kubernetes"])  # 자바스크립트는 Java 가 아니다
        self.assertEqual(find_skills("자바스크립트 도구Docker AWS2"), ["JavaScript"])

    def test_longest_mention_wins_and_distinct_mentions_still_count(self):
        text = "Elastic Kubernetes Service와 SQL Server, AWS Lambda 사용"
        self.assertEqual(find_skills(text), ["EKS", "Lambda", "MSSQL"])
        self.assertEqual(
            find_skills(text + ". 일반 SQL과 Kubernetes도 사용"), ["EKS", "Kubernetes", "Lambda", "MSSQL", "SQL"]
        )

    def test_case_and_punctuation_follow_the_dictionary(self):
        self.assertEqual(find_skills("amazon-web-services, nextjs, CI_CD, 카프카"), ["AWS", "CI/CD", "Kafka", "Next.js"])

    def test_go_in_course_instructions_is_not_a_language_requirement(self):
        self.assertEqual(find_skills("learn how to go about it. Go directly to the assessment. Go to the next lab."), [])
        self.assertEqual(find_skills("Go로 API를 개발합니다."), ["Go"])
        self.assertEqual(find_skills("golang"), ["Go"])


class SimilarNamesStayApartTest(unittest.TestCase):
    """이름이 비슷한 기술을 한 이름으로 합치지 않는다. 임베딩 추천(pipeline/suggest.py)은 JavaScript 의 1순위 후보로 Java 를
    올리므로(0.657), 사전에 독립 항목이 없으면 사람이 Java 로 잘못 합칠 수 있다(2026-11-20)."""

    def test_javascript_is_its_own_skill_not_java(self):
        for raw in ("JavaScript", "Javascript", "자바스크립트"):
            self.assertEqual(normalize_skill(raw), "JavaScript", raw)
        self.assertEqual(normalize_skill("Java"), "Java")
        self.assertEqual(normalize_skill("자바"), "Java")
        self.assertEqual(find_skills("자바스크립트와 자바를 함께 씁니다"), ["Java", "JavaScript"])
        self.assertEqual(find_skills("JavaScript 기초"), ["JavaScript"])

    def test_spring_and_spring_boot_are_separate_skills(self):
        for raw in ("Spring Boot", "SpringBoot", "스프링 부트", "스프링부트"):
            self.assertEqual(normalize_skill(raw), "Spring Boot", raw)
        for raw in ("Spring", "스프링"):
            self.assertEqual(normalize_skill(raw), "Spring", raw)
        self.assertEqual(find_skills("Spring Boot로 서버를 개발합니다"), ["Spring Boot"])  # 더 긴 이름이 이긴다
        self.assertEqual(find_skills("Spring과 Spring Boot 경험"), ["Spring", "Spring Boot"])


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
    # 비슷해도 합치지 않는다: 사전에 독립 항목으로 둔다(사전에서 빼 두면 어디서도 세지 못한다)
    assert normalize_skill("JavaScript") == "JavaScript" != normalize_skill("Java")
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
    unittest.main()
