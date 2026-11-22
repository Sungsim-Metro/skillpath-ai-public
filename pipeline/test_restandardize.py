"""pipeline/restandardize.py 점검. S3·DynamoDB 없이 plan() 에 dict 를 넣어 확인한다.
실행: python -m unittest pipeline.test_restandardize
"""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.restandardize import check_writable, plan, publish_revision, restore, restandardize

TEXT = "자격요건\n- Docker 와 Kubernetes 운영 경험\n- ZzzUnknownTool 사용 경험"


def skill(raw, evidence, confidence="high", normalized=None):
    return {"raw": raw, "normalized": normalized or raw, "type": "required", "section": None,
            "evidence": evidence, "confidence": confidence, "ncs_unit": None}


def item(skills):
    return {"posting_id": "p1", "extraction": {"posting_id": "p1", "skills": skills}, "analysis": {}}


class RestandardizeTest(unittest.TestCase):
    def test_evidence_not_in_text_becomes_uncertain(self):
        old = item([skill("Docker", "본문에 없는 문장입니다")])
        changed, summary = plan([old], {"p1": TEXT})
        self.assertEqual(changed[0]["extraction"]["skills"][0]["confidence"], "uncertain")
        self.assertEqual(summary["changed"], 1)

    def test_idempotent_on_already_standardized(self):
        new = restandardize(item([skill("도커", "Docker 와 Kubernetes 운영 경험")])["extraction"], TEXT, "v2.1")
        self.assertEqual(restandardize(new, TEXT, "v2.1"), new)
        changed, summary = plan([{**item([]), "extraction": new}], {"p1": TEXT})
        self.assertEqual(changed, [])
        self.assertEqual(summary["same"], 1)

    def test_unknown_skill_stays_uncertain(self):
        new = restandardize(item([skill("ZzzUnknownTool", "ZzzUnknownTool 사용 경험")])["extraction"], TEXT, "v2.1")
        self.assertEqual(new["skills"][0]["confidence"], "uncertain")

    def test_check_writable_rejects_float(self):
        check_writable([{"a": 1, "b": "x", "c": None}])
        with self.assertRaises(TypeError):
            check_writable([{"a": 0.5}])

    def test_restore_dry_run_writes_nothing_then_apply_writes_demand_last(self):
        class Store:
            def __init__(self): self.calls = []
            def stage_extraction(self, partition, i): self.calls.append("e")
            def get_demand_snapshot(self, job): return {"snapshot_id": "base"}
            def publish_batch(self, s, base): self.calls.append((base, s["restored_from"], s["snapshot_id"]))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            valid = {"job_category": "j", "posting_id": "p1", "input_fingerprint": "f"}
            counted = [{"posting_id": "p1", "input_fingerprint": "f"}]
            backup = {"job_category": "j", "snapshot_id": "s", "demand": {"total_postings": 1},
                      "counted_postings": counted}
            inputs = d / "j-extractions-S.json"
            (d / "j-demand-S.json").write_text(json.dumps(backup), encoding="utf-8")
            store = Store()
            for invalid in ([], [valid, valid], [{**valid, "input_fingerprint": "different"}]):
                inputs.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    restore(store, d, "j", "S", apply=True)
                self.assertEqual(store.calls, [])
            inputs.write_text(json.dumps([valid, {**valid, "posting_id": "extra"}]))
            restore(store, d, "j", "S", apply=False)
            self.assertEqual(store.calls, [])
            restore(store, d, "j", "S", apply=True)
            self.assertEqual(store.calls[0], "e")
            self.assertEqual(store.calls[1][:2], ("base", "s"))
            self.assertTrue(store.calls[1][2].startswith("restore-"))

    def test_revision_of_versioned_snapshot_stages_every_row_in_new_partition(self):
        from unittest.mock import Mock

        store = Mock()
        current = {"snapshot_id": "old", "extraction_partition": "run#j#old"}
        snapshot = {"job_category": "j", "snapshot_id": "new"}
        records = [{"posting_id": "p1"}, {"posting_id": "p2"}]
        publish_revision(store, current, snapshot, records)
        self.assertEqual(store.stage_extraction.call_count, 2)
        store.save_extraction.assert_not_called()
        store.publish_batch.assert_called_once_with({**snapshot, "extraction_partition": "run#j#new"}, "old")


if __name__ == "__main__":
    unittest.main()
