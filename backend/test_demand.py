"""고유 공고 집계와 입력 불일치·명시적 제외를 확인한다."""
from __future__ import annotations

import unittest

from backend.demand import build_snapshot
from backend.jobs import validate_role


def skill(name: str, kind: str = "required", confidence: str = "high") -> dict:
    return {"raw": name, "normalized": name, "type": kind,
            "evidence": f"{name} experience", "confidence": confidence, "ncs_unit": None}


def record(posting_id: str, skills: list[dict]) -> dict:
    return {
        "job_category": "cloud_engineer", "posting_id": posting_id,
        "input_fingerprint": f"fingerprint-{posting_id}", "source": "rocketpunch",
        "posting_meta": {"url": None}, "extraction": {"posting_id": posting_id, "skills": skills},
    }


def snapshot(items: list[dict], selected: dict[str, str] | None = None, exclusions: dict | None = None) -> dict:
    if selected is None:
        selected = {item["posting_id"]: item["input_fingerprint"] for item in items}
    return build_snapshot(
        items, selected=selected, job_category="cloud_engineer", collected="2026-11-15",
        exclusions=exclusions or {}, selection_version="cloud-aws-v1", run_id="test-run",
    )


class DemandAggregationTest(unittest.TestCase):
    def test_repeated_postings_skills_and_runs_do_not_inflate_counts(self) -> None:
        item = record("rp-1", [skill("Docker"), skill("Docker"), skill("Linux")])
        result = snapshot([item, item])
        self.assertEqual(result["demand"]["total_postings"], 1)
        self.assertEqual(len(result["counted_postings"]), 1)
        for row in result["demand"]["skills"]:
            self.assertEqual(row["postings"], 1)
            self.assertEqual(row["required"] + row["preferred"], row["postings"])
        self.assertEqual(result["demand"], snapshot([item])["demand"])

    def test_high_preferred_survives_uncertain_required(self) -> None:
        result = snapshot([record("rp-1", [
            skill("Docker", "required", "uncertain"), skill("Docker", "preferred"),
        ])])
        row = result["demand"]["skills"][0]
        self.assertEqual((row["required"], row["preferred"]), (0, 1))
        self.assertEqual(result["quality"]["uncertain_skills"], [])

    def test_empty_and_uncertain_successes_are_in_denominator(self) -> None:
        result = snapshot([
            record("rp-1", []), record("rp-2", [skill("Terraform", confidence="uncertain")]),
            record("rp-3", [skill("Docker")]),
        ])
        self.assertEqual(result["demand"]["total_postings"], 3)
        self.assertEqual(len(result["demand"]["skills"]), 1)
        self.assertEqual(result["quality"]["empty_extraction_postings"], 1)
        self.assertEqual(result["quality"]["uncertain_only_postings"], 1)
        self.assertEqual(result["quality"]["uncertain_skills"], [{"skill": "Terraform", "postings": 1}])

    def test_reextraction_and_exclusion_remove_previous_counts(self) -> None:
        old = record("rp-1", [skill("Docker")])
        current = record("rp-1", [skill("Linux")])
        current["input_fingerprint"] = "changed"
        excluded = record("rp-2", [skill("Terraform")])
        result = snapshot(
            [old, current, excluded], selected={"rp-1": "changed"},
            exclusions={"rp-2": "invalid response"},
        )
        self.assertEqual([row["skill"] for row in result["demand"]["skills"]], ["Linux"])
        self.assertEqual(result["quality"]["eligible_postings"], 2)
        self.assertEqual(result["quality"]["excluded_postings"], [{"posting_id": "rp-2", "reason": "invalid response"}])

    def test_missing_or_stale_results_prevent_snapshot(self) -> None:
        with self.assertRaises(ValueError):
            snapshot([record("rp-1", [])], selected={"rp-1": "changed"})
        with self.assertRaises(ValueError):
            snapshot([], selected={"rp-1": "missing"})

    def test_evidence_is_deterministic_and_limited_to_three_postings(self) -> None:
        items = [record(f"rp-{index}", [skill("Docker")]) for index in range(5)]
        forward = snapshot(items)["demand"]
        reverse = snapshot(list(reversed(items)))["demand"]
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward["skills"][0]["evidence_samples"]), 3)

    def test_invalid_role_and_overlapping_exclusion_are_rejected(self) -> None:
        validate_role("cloud_engineer", "클라우드 엔지니어")
        with self.assertRaises(ValueError):
            validate_role("cloud_engineer", "다른 직무")
        with self.assertRaises(ValueError):
            snapshot([record("rp-1", [])], exclusions={"rp-1": "excluded"})


if __name__ == "__main__":
    unittest.main()
