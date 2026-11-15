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

    # NCS 단위: 확신 있는 연결(S3)만 채우고, 없는 기술(Docker)과 모르는 표현(Rust)은 None
    out, _ = standardize({"posting_id": "x", "skills": [skill("S3", "S3"), skill("도커", "도커"), skill("Rust", "Rust")]})
    assert [s["ncs_unit"] for s in out["skills"]] == ["2001021507", None, None]

    # 합칠 때는 가중치가 가장 큰 것 하나(칸이 있으면 칸 기준): 자격요건(1.0)이 주요업무(0.7)보다 크다.
    # 이전 규칙(required 우선)이면 주요업무의 required 가 남는다
    out, _ = standardize({"posting_id": "x", "skills": [
        {**skill("k8s", "Kubernetes", "required"), "section": "duty"},
        {**skill("쿠버네티스", "쿠버네티스", "preferred"), "section": "qualification"}]})
    assert [(s["normalized"], s["section"]) for s in out["skills"]] == [("Kubernetes", "qualification")]
    # 가중치가 같으면 먼저 나온 것을 남긴다
    out, _ = standardize({"posting_id": "x", "skills": [
        {**skill("Docker", "Docker", "required"), "section": "other"}, skill("도커", "도커", "required")]})
    assert out["skills"][0]["raw"] == "Docker"
    # 둘 다 required 여도 칸이 다르면 칸 기준: 주요업무(0.7)가 우대사항(0.5)보다 커서 주요업무가 남는다(type 이 같아도 칸이 이긴다)
    out, _ = standardize({"posting_id": "x", "skills": [
        {**skill("k8s", "Kubernetes", "required"), "section": "preference"},
        {**skill("쿠버네티스", "쿠버네티스", "required"), "section": "duty"}]})
    assert [(s["raw"], s["section"]) for s in out["skills"]] == [("쿠버네티스", "duty")]

    # 칸이 없으면 type 기준 가중치(required 1.0 > preferred 0.5)로 합친다, 같은 모르는 표현은 한 번만
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
