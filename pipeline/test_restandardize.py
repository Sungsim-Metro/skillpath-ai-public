"""pipeline/restandardize.py 점검. S3·DynamoDB 없이 plan() 에 dict 를 넣어 확인한다.
실행: python -m unittest pipeline.test_restandardize
"""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.restandardize import check_writable, plan, restore, restandardize

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
            def save_extraction(self, i): self.calls.append("e")
            def save_demand(self, s): self.calls.append("d")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "j-extractions-S.json").write_text(json.dumps([{"posting_id": "p1"}]), encoding="utf-8")
            (d / "j-demand-S.json").write_text(json.dumps({"snapshot_id": "s"}), encoding="utf-8")
            store = Store()
            restore(store, d, "j", "S", apply=False)
            self.assertEqual(store.calls, [])
            restore(store, d, "j", "S", apply=True)
            self.assertEqual(store.calls, ["e", "d"])


if __name__ == "__main__":
    unittest.main()
