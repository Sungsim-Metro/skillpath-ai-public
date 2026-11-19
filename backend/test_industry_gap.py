import codecs
import csv
import io
import unittest

from backend.industry_gap import (
    aggregate_supply,
    analyze_gap,
    analyze_gap_from_supply,
    analyze_job_gap_from_supply,
    calculate_gap,
    extract_skills,
    gap_csv,
    job_gap_csv,
    posting_weights,
)


def course(name: str, content: str = "", goal: str = "") -> dict[str, str]:
    return {"title": name, "description": content, "objectives": goal}


class IndustryGapTest(unittest.TestCase):
    def test_job_gap_uses_each_roles_own_demand_and_the_same_catalog(self) -> None:
        postings = [
            {"job_category": "ai_engineer", "industry": "금융", "text": "Python"},
            {"job_category": "cloud_engineer", "industry": "금융", "text": "Docker"},
            {"job_category": "cloud_engineer", "industry": "IT", "text": "AWS"},
        ]
        supply = aggregate_supply([course("Docker")], source="교육과정")
        result = analyze_job_gap_from_supply(postings, supply)
        self.assertEqual(
            [(r["job_category"], r["skill"], r["demand_pct"], r["supply_pct"], r["gap_pp"])
             for r in result["rows"]],
            [("ai_engineer", "Docker", 0.0, 100.0, -100.0),
             ("ai_engineer", "Python", 100.0, 0.0, 100.0),
             ("cloud_engineer", "AWS", 50.0, 0.0, 50.0),
             ("cloud_engineer", "Docker", 50.0, 100.0, -50.0)],
        )
        self.assertEqual(result["top3"]["ai_engineer"][0], {"skill": "Python", "gap_pp": 100.0})
        self.assertEqual(result["top3"]["cloud_engineer"][0], {"skill": "AWS", "gap_pp": 50.0})
        exported = csv.DictReader(io.StringIO(job_gap_csv(result["rows"]).decode("utf-8-sig")))
        self.assertEqual(exported.fieldnames, ["job_category", "skill", "demand_pct", "supply_pct", "gap_pp"])
        self.assertEqual([r["job_category"] for r in exported], ["ai_engineer"] * 2 + ["cloud_engineer"] * 2)

    def test_supply_is_available_without_job_postings(self) -> None:
        courses = [course("Docker", "도커", "Practice Docker and SQL"), course("SQL"), course("무관")]
        supply = aggregate_supply(courses, source="테스트 목록")
        self.assertEqual(supply, {
            "source": "테스트 목록", "total_courses": 3,
            "skills": [{"skill": "Docker", "courses": 1}, {"skill": "SQL", "courses": 2}],
        })
        self.assertEqual(aggregate_supply([], source="빈 목록"),
                         {"source": "빈 목록", "total_courses": 0, "skills": []})

    def test_gap_uses_the_independent_supply_counts(self) -> None:
        postings = [{"industry": "유통", "text": "AWS"}]
        courses = [course("Docker", "Docker", "AWS"), course("AWS"), course("AWS, SQL")]
        supply = aggregate_supply(courses, source="테스트 목록")
        result = analyze_gap_from_supply(postings, supply)
        self.assertEqual([(r["skill"], r["supply_pct"]) for r in result["rows"]],
                         [("AWS", 60.0), ("Docker", 20.0), ("SQL", 20.0)])
        self.assertEqual(result, analyze_gap(postings, courses))

    def test_supply_counts_each_program_once_per_skill_across_providers(self) -> None:
        courses = [
            {**course("Docker", "AWS and Docker", "Practice with Docker"), "provider": "A", "prerequisites": "SQL"},
            {**course("SQL", "Python", ""), "provider": "Azure", "aws_services": "SageMaker"},
        ]
        rows = calculate_gap([{"industry": "금융", "text": "AWS"}], courses)
        self.assertEqual({r["skill"]: r["supply_pct"] for r in rows},
                         {"AWS": 25.0, "Docker": 25.0, "Python": 25.0, "SQL": 25.0})

    def test_reference_example(self) -> None:
        postings = [
            {"industry": "예시", "text": "[주요업무]\nDocker, k8s\n[자격요건]\nKubernetes, K8S\n"
             "[우대사항]\nDocker, AWS (필수 아님)"},
            {"industry": "예시", "text": "[자격요건]\nDocker\n[주요업무]\naws"},
        ]
        courses = [course("도커 기초", "Docker", "Python"), course("분석 실습", "파이썬")]
        rows = calculate_gap(postings, courses)
        self.assertEqual(
            [(r["skill"], r["demand_pct"], r["supply_pct"], r["gap_pp"]) for r in rows],
            [("AWS", 30.8, 0.0, 30.8), ("Docker", 43.6, 33.3, 10.3),
             ("Kubernetes", 25.6, 0.0, 25.6), ("Python", 0.0, 66.7, -66.7)],
        )
        self.assertTrue(all(r["industry"] == "예시" for r in rows))

    def test_dictionary_aliases_in_demand_and_supply(self) -> None:
        text = "postgres와 docker / 자바스크립트 Dockerfile"
        self.assertEqual(posting_weights(text), {"PostgreSQL": 0.5, "Docker": 0.5})
        supply = aggregate_supply([course(text, "PostgreSQL")], source="테스트 목록")
        self.assertEqual({row["skill"]: row["courses"] for row in supply["skills"]},
                         {"PostgreSQL": 1, "Docker": 1})

    def test_dictionary_aliases_and_boundaries(self) -> None:
        self.assertEqual(extract_skills("k8s와 K8S 운영 / AWS가 필수 아님"), ["AWS", "Kubernetes"])
        self.assertEqual(extract_skills("도구Docker Dockerfile PYTHON Amazon-Web-Services"), ["AWS", "Python"])
        self.assertEqual(
            extract_skills("IaC(Terraform) 컨테이너(Docker) 지속적 통합·배포"),
            ["CI/CD", "Docker", "Terraform"],
        )

    def test_benefits_and_company_introduction_do_not_count_as_job_demand(self) -> None:
        text = "[자격요건]\nPython\n[혜택 및 복지]\nAWS 교육비 지원\n[회사소개]\nKubernetes 기반"
        self.assertEqual(posting_weights(text), {"Python": 1.0})

    def test_common_subheadings_and_section_transitions(self) -> None:
        text = ("[복리후생]\r\nSQL\r\n[기술 역량]\r\nAWS\r\n[담당업무]\r\nPython\r\n"
                "[우대조건]\r\nAWS, Docker\r\n[채용절차]\r\nKubernetes")
        self.assertEqual(posting_weights(text), {"AWS": 1.0, "Python": 0.7, "Docker": 0.5})

    def test_repeated_headings_keep_earlier_mentions_and_highest_weight(self) -> None:
        text = (
            "React\n[주요업무]\nAzure\n  [주요업무] SQL\n- Docker k8s -\n"
            "[우대사항]\nDocker\n[자격요건]\nK8S\n[새 구역]\nAWS"
        )
        self.assertEqual(posting_weights(text), {"React": 0.5, "Azure": 0.7, "Docker": 0.7,
                                               "Kubernetes": 1.0, "AWS": 0.5})

    def test_industry_order_duplicates_and_global_supply(self) -> None:
        duplicate = {"id": "same", "industry": "유통", "text": "Docker"}
        postings = [
            {"industry": "제조", "text": "", "skills_rule": ["SQL"]},
            {"industry": "공공", "text": "", "title": "Docker"},
            duplicate, duplicate, {"industry": "유통", "text": "AWS"},
            {"industry": "제조", "text": "SQL"},
        ]
        rows = calculate_gap(postings, [course("Amazon", "Web", "Services"), course("")])
        self.assertEqual(
            [(r["industry"], r["skill"], r["demand_pct"], r["supply_pct"]) for r in rows],
            [("유통", "AWS", 33.3, 100.0), ("유통", "Docker", 66.7, 0.0),
             ("제조", "AWS", 0.0, 100.0), ("제조", "SQL", 100.0, 0.0)],
        )

    def test_empty_or_unmatched_inputs(self) -> None:
        self.assertEqual(calculate_gap([], [course("AWS")]), [])
        self.assertEqual(calculate_gap([{"industry": "금융", "text": "무관"}], [course("SQL")]), [])
        for courses in ([], [course("무관")]):
            with self.subTest(courses=courses):
                rows = calculate_gap([{"industry": "금융", "text": "Azure"}], courses)
                self.assertEqual(rows, [{"industry": "금융", "skill": "Azure", "demand_pct": 100.0,
                                         "supply_pct": 0.0, "gap_pp": 100.0}])

    def test_csv_and_top_three_use_distinct_rounding(self) -> None:
        postings = [{"industry": "금융", "text": name} for name in ["AWS"] + ["SQL"] * 4]
        result = analyze_gap(postings, [course("AWS")] * 3 + [course("SQL")] * 13)
        aws = result["rows"][0]
        self.assertEqual((aws["demand_pct"], aws["supply_pct"], aws["gap_pp"]), (20, 18.8, 1.2))
        self.assertEqual(result["top3"]["금융"][0], {"skill": "AWS", "gap_pp": 1.3})
        self.assertLess(result["top3"]["금융"][1]["gap_pp"], 0)

    def test_python_311_rounding_is_preserved_in_newer_runtimes(self) -> None:
        postings = [{"industry": "금융", "text": name}
                    for name in ["AWS"] * 13 + ["Docker"] * 27 + ["SQL"] * 8]
        docker = next(row for row in calculate_gap(postings, []) if row["skill"] == "Docker")
        self.assertEqual((docker["demand_pct"], docker["gap_pp"]), (56.3, 56.3))

    def test_unmatched_rows_remain_in_the_rate_denominators(self) -> None:
        names = ["AWS"] + ["SQL"] * 15 + [""] * 6
        rows = calculate_gap(
            [{"industry": "유통", "text": name} for name in names],
            [course(name) for name in names],
        )
        self.assertEqual((rows[0]["demand_pct"], rows[0]["supply_pct"]), (6.3, 6.3))

    def test_retail_aws_reference_totals(self) -> None:
        postings = []
        for index in range(22):
            required = ["Python", "SQL", "Docker"]
            duties = []
            if index < 7:
                required.extend(["Terraform", "AWS"])
            elif index < 10:
                duties.append("AWS")
            if index < 6:
                duties.append("Azure")
            text = f"[자격요건]\n{' '.join(required)}\n[주요업무]\n{' '.join(duties)}"
            postings.append({"industry": "유통", "text": text})
        courses = [course("AWS")] * 2 + [course("SQL")] * 34 + [course("")] * 12
        rows = calculate_gap(postings, courses)
        self.assertEqual(rows[0], {"industry": "유통", "skill": "AWS", "demand_pct": 10.5,
                                   "supply_pct": 5.6, "gap_pp": 5.0})

    def test_top_three_limit_and_csv_encoding(self) -> None:
        result = analyze_gap([{"industry": "교육,연구", "text": "AWS"}],
                             [course("SQL"), course("Docker"), course("Azure")])
        self.assertEqual(len(result["top3"]["교육,연구"]), 3)
        self.assertEqual(result["top3"]["교육,연구"][0]["skill"], "AWS")
        content = gap_csv(result["rows"])
        self.assertTrue(content.startswith(codecs.BOM_UTF8))
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        self.assertEqual(reader.fieldnames, ["industry", "skill", "demand_pct", "supply_pct", "gap_pp"])
        self.assertEqual(next(reader), {"industry": "교육,연구", "skill": "AWS", "demand_pct": "100.0",
                                       "supply_pct": "0.0", "gap_pp": "100.0"})


if __name__ == "__main__":
    unittest.main()
