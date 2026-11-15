"""standardize 확인. 실행: python -m pipeline.test_standardize"""
import copy
import json
from pathlib import Path

from pipeline.standardize import standardize

GOLD = Path(__file__).resolve().parent.parent / "ai" / "eval" / "gold_extraction.json"


def skill(raw, normalized, type="required", confidence="high"):
    return {"raw": raw, "normalized": normalized, "type": type, "evidence": f"{raw} 경험",
            "confidence": confidence, "ncs_unit": None}


def demo():
    # 사전에 있으면 대표 이름으로, 없으면 그대로 두고 uncertain, 목록에 모은다
    src = {"posting_id": "x", "skills": [skill("k8s", "k8s"), skill("Rust", "Rust")]}
    before = copy.deepcopy(src)
    out, unknown = standardize(src)
    assert src == before  # 입력은 고치지 않는다
    assert out["posting_id"] == "x"
    assert [s["normalized"] for s in out["skills"]] == ["Kubernetes", "Rust"]
    assert [s["confidence"] for s in out["skills"]] == ["high", "uncertain"]
    assert unknown == ["Rust"]
    assert {s["raw"] for s in out["skills"]} == {"k8s", "Rust"}  # raw 는 그대로

    # 합쳐져 같은 기술이 되면 required 를 남긴다, 같은 모르는 표현은 한 번만
    out, unknown = standardize({"posting_id": "x", "skills": [
        skill("Docker", "Docker", "preferred"), skill("도커", "도커", "required"),
        skill("Rust", "Rust"), skill("Rust", "Rust")]})
    assert [(s["normalized"], s["type"]) for s in out["skills"]] == [("Docker", "required"), ("Rust", "required")]
    assert unknown == ["Rust"]

    # 성진의 정답지: LLM 이름이 엉망이어도(normalized = raw) 정답지의 normalized 로 맞춰진다
    for posting in json.loads(GOLD.read_text(encoding="utf-8")):
        skills = [skill(s["raw"], s["raw"], s["type"]) for s in posting["skills"]]
        out, unknown = standardize({"posting_id": posting["posting_id"], "skills": skills})
        assert not unknown, (posting["posting_id"], unknown)
        got = {(s["normalized"], s["type"]) for s in out["skills"]}
        want = {(s["normalized"], s["type"]) for s in posting["skills"]}
        assert got == want, (posting["posting_id"], got ^ want)


if __name__ == "__main__":
    demo()
    print("통과")
