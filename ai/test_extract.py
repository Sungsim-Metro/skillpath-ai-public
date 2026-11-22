"""기술 후보 선택과 저장용 입력 식별자 회귀 검사. 외부 모델은 호출하지 않는다."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import extract
from ai.bedrock import ConverseResult


def candidate(
    skill: str = "Docker",
    kind: str = "preferred",
    evidence: str = "Docker 운영 경험 우대",
) -> dict:
    return {
        "raw": skill,
        "normalized": skill,
        "type": kind,
        "evidence": evidence,
    }


class CandidateSelectionTest(unittest.TestCase):
    def test_valid_preferred_survives_invalid_required_in_both_orders(self) -> None:
        valid = candidate()
        invalid = candidate(kind="required", evidence="본문에 없는 Docker 필수 문장")
        for items in ([valid, invalid], [invalid, valid]):
            with self.subTest(items=items):
                result = extract.to_contract(items, valid["evidence"])
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["type"], "preferred")
                self.assertEqual(result[0]["confidence"], "high")
                self.assertEqual(result[0]["evidence"], valid["evidence"])

    def test_contract_example_prefers_verified_candidate_over_higher_weight(self) -> None:
        valid = candidate("AWS", evidence="AWS 운영 경험 우대")
        invalid = candidate("AWS", kind="required", evidence="AWS 운영 경험 필수")
        posting = {"id": "sample-0002", "text": "[우대사항]\nAWS 운영 경험 우대"}
        example_path = (
            Path(__file__).resolve().parent.parent
            / "data" / "sample" / "extraction_deduplicated.json"
        )
        expected = json.loads(example_path.read_text(encoding="utf-8"))
        for version in extract.PROMPTS:
            for items in ([valid, invalid], [invalid, valid]):
                with self.subTest(version=version, items=items):
                    result = {
                        "posting_id": posting["id"],
                        "skills": extract.to_contract(items, posting["text"], version),
                    }
                    self.assertEqual(result, expected)

    def test_valid_required_wins_between_verified_candidates(self) -> None:
        preferred = candidate()
        required = candidate(kind="required", evidence="Docker 운영 경험 필수")
        text = f"{preferred['evidence']}\n{required['evidence']}"
        for items in ([preferred, required], [required, preferred]):
            with self.subTest(items=items):
                result = extract.to_contract(items, text)
                self.assertEqual(result[0]["type"], "required")
                self.assertEqual(result[0]["confidence"], "high")

    def test_unverified_candidate_is_preserved_once(self) -> None:
        result = extract.to_contract(
            [candidate(), candidate(kind="required")], "Linux 운영 경험"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["confidence"], "uncertain")
        self.assertEqual(result[0]["type"], "required")

    def test_unknown_skill_is_uncertain_with_verbatim_evidence(self) -> None:
        item = candidate("UnlistedTool", evidence="UnlistedTool 사용 경험")
        result = extract.to_contract([item], item["evidence"])
        self.assertEqual(result[0]["confidence"], "uncertain")

    def test_empty_names_and_evidence_are_omitted(self) -> None:
        result = extract.to_contract(
            [candidate("  "), candidate(evidence=" \n ")], "Docker 운영 경험"
        )
        self.assertEqual(result, [])

    def test_first_equal_candidate_and_contract_fields_are_preserved(self) -> None:
        first = candidate(evidence=" Docker 운영 경험 우대 ")
        second = candidate(evidence="Docker 도입 경험 우대")
        text = "Docker 운영 경험 우대\nDocker 도입 경험 우대"
        result = extract.to_contract([first, second], text)
        self.assertEqual(result, [{
            "raw": "Docker",
            "normalized": "Docker",
            "type": "preferred",
            "section": None,
            "evidence": "Docker 운영 경험 우대",
            "confidence": "high",
            "ncs_unit": None,
        }])

    def test_section_weight_wins_between_verified_candidates(self) -> None:
        duty = candidate(kind="preferred", evidence="Docker 운영 자동화")
        preference = candidate(kind="required", evidence="Docker 자격증 우대")
        text = "[주요업무]\nDocker 운영 자동화\n[우대사항]\nDocker 자격증 우대"
        for version in extract.PROMPTS:
            for items in ([duty, preference], [preference, duty]):
                with self.subTest(version=version, items=items):
                    result = extract.to_contract(items, text, version)
                    self.assertEqual(result[0]["section"], "duty")
                    self.assertEqual(result[0]["evidence"], duty["evidence"])


class FingerprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"BEDROCK_MODEL_ID": "test-model"}).start()
        self.posting = {"id": "rp-1", "text": "Docker 운영 경험 우대"}

    def test_settings_are_float_free_and_match_inference_constants(self) -> None:
        settings = extract.extraction_settings()
        self.assertEqual(settings["model_id"], "test-model")
        self.assertEqual(settings["extractor_version"], extract.EXTRACTOR_VERSION)
        self.assertEqual(settings["prompt_version"], extract.PROMPT_VERSION)
        self.assertEqual(settings["inference_config"], {
            "temperature": str(extract.TEMPERATURE),
            "max_tokens": extract.MAX_TOKENS,
        })
        self.assertEqual(len(settings["canonical_skills_sha256"]), 64)

    def test_default_prompt_is_v3_and_changes_fingerprint_from_v1(self) -> None:
        # 11/22 두 직무 저장 추출을 v3 로 다시 만들고 기본값을 v3 로 맞췄다. 배치가 저장하는 analysis 는 extraction_settings 를 쓴다
        self.assertEqual(extract.DEFAULT_PROMPT, "v3")
        self.assertEqual(extract.extraction_settings()["prompt_version"], "extract_v3")
        v3 = extract.input_fingerprint(self.posting, "cloud_engineer")
        # 저장된 항목의 지문은 v1 기준이다. 기본값이 바뀌면 지문이 달라 extraction_batch 가 전체를 다시 추출한다
        with patch.object(extract, "DEFAULT_PROMPT", "v1"):
            self.assertEqual(extract.extraction_settings()["prompt_version"], "extract_v1")
            self.assertNotEqual(v3, extract.input_fingerprint(self.posting, "cloud_engineer"))

    def test_missing_or_blank_model_id_is_rejected(self) -> None:
        for model_id in ("", "  "):
            with self.subTest(model_id=model_id):
                with patch.dict(os.environ, {"BEDROCK_MODEL_ID": model_id}):
                    with self.assertRaises(ValueError):
                        extract.extraction_settings()

    def test_same_current_input_and_settings_have_same_fingerprint(self) -> None:
        settings = extract.extraction_settings()
        original = extract.input_fingerprint(self.posting, "cloud_engineer")
        reordered = dict(reversed(list(settings.items())))
        self.assertEqual(
            original,
            extract.input_fingerprint(self.posting, "cloud_engineer", reordered),
        )
        self.assertEqual(len(original), 64)
        self.assertEqual(
            original,
            extract.input_fingerprint(
                {**self.posting, "title": "새 제목"}, "cloud_engineer"
            ),
        )

    def test_analysis_input_and_job_category_changes_invalidate_result(self) -> None:
        original = extract.input_fingerprint(self.posting, "cloud_engineer")
        changed = [
            ({**self.posting, "id": "rp-2"}, "cloud_engineer"),
            ({**self.posting, "text": "Docker 운영 경험 필수"}, "cloud_engineer"),
            (self.posting, "different_job"),
        ]
        for posting, category in changed:
            with self.subTest(posting=posting, category=category):
                self.assertNotEqual(
                    original, extract.input_fingerprint(posting, category)
                )

    def test_model_versions_and_inference_changes_invalidate_result(self) -> None:
        original = extract.input_fingerprint(self.posting, "cloud_engineer")
        settings_changes = (
            ("EXTRACTOR_VERSION", "extractor-next"),
            ("MAX_TOKENS", 2048),
            ("TEMPERATURE", 0.5),
        )
        for name, value in settings_changes:
            with self.subTest(name=name):
                with patch.object(extract, name, value):
                    self.assertNotEqual(
                        original,
                        extract.input_fingerprint(self.posting, "cloud_engineer"),
                    )
        with patch.object(extract.prompt_of(extract.DEFAULT_PROMPT), "VERSION", "prompt-next"):
            self.assertNotEqual(
                original, extract.input_fingerprint(self.posting, "cloud_engineer")
            )
        with patch.dict(os.environ, {"BEDROCK_MODEL_ID": "another-model"}):
            self.assertNotEqual(
                original, extract.input_fingerprint(self.posting, "cloud_engineer")
            )

    def test_current_system_and_message_changes_invalidate_result(self) -> None:
        original = extract.input_fingerprint(self.posting, "cloud_engineer")
        prompt = extract.prompt_of(extract.DEFAULT_PROMPT)
        with patch.object(prompt, "SYSTEM", "새 추출 규칙"):
            self.assertNotEqual(
                original, extract.input_fingerprint(self.posting, "cloud_engineer")
            )
        with patch.object(prompt, "user_message", return_value="새 입력 형식"):
            self.assertNotEqual(
                original, extract.input_fingerprint(self.posting, "cloud_engineer")
            )

    def test_actual_canonical_skill_changes_invalidate_result(self) -> None:
        original = extract.input_fingerprint(self.posting, "cloud_engineer")
        prompt = extract.prompt_of(extract.DEFAULT_PROMPT)
        names = prompt.CANONICAL_SKILLS + ("UnlistedTool",)
        with patch.object(prompt, "CANONICAL_SKILLS", names):
            self.assertNotEqual(
                original, extract.input_fingerprint(self.posting, "cloud_engineer")
            )

    def test_extract_uses_settings_for_the_real_request(self) -> None:
        response = ConverseResult(
            {"skills": [candidate()]},
            attempts=1,
            usage={"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
        )
        with patch.object(extract, "converse_json", return_value=response) as converse:
            result, meta = extract.extract_with_meta(self.posting)
        self.assertEqual(result["posting_id"], "rp-1")
        self.assertEqual(result["skills"][0]["confidence"], "high")
        self.assertEqual(meta.usage["totalTokens"], 6)
        kwargs = converse.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], extract.MAX_TOKENS)
        self.assertEqual(kwargs["temperature"], extract.TEMPERATURE)
        self.assertEqual(kwargs["mock_key"], "rp-1")
        self.assertIs(kwargs["validate"], extract.validate_llm_output)


if __name__ == "__main__":
    unittest.main()
