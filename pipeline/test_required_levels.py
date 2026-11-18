"""기준 단계 계산 확인(가짜 벡터). 실행: python -m unittest pipeline.test_required_levels"""
import os
import unittest
from unittest import mock

os.environ["BEDROCK_MOCK"] = "1"

from backend.levels import DEFAULT_REQUIRED_LEVEL, level_guide  # noqa: E402
from pipeline import required_levels as rl  # noqa: E402


def _posting(*items):
    return {"skills": [{"normalized": s, "evidence": e, "confidence": c} for s, e, c in items]}


class RequiredLevelsTest(unittest.TestCase):
    def test_median_of_posting_levels(self):
        found = {"Docker": [2, 3, 3, 5], "Git": [1, 4]}
        out = rl.summarize(found)
        self.assertEqual(out["Docker"]["required_level"], 3)
        self.assertEqual(out["Docker"]["confidence"], "certain")
        self.assertEqual(out["Docker"]["distribution"], {"2": 1, "3": 2, "5": 1})
        # 표본이 MIN_POSTINGS 미만이면 기본 단계와 불확실
        self.assertEqual(out["Git"]["required_level"], DEFAULT_REQUIRED_LEVEL)
        self.assertEqual(out["Git"]["confidence"], "uncertain")

    def test_example_sentence_matches_its_level(self):
        # 단계 예시와 같은 문장이면 그 단계로 잡힌다(가짜 벡터도 같은 문장은 유사도 1)
        text = level_guide()["subskills"]["C1-4"]["levels"]["4"]
        found = rl.posting_levels([_posting(("Docker", text, "high"))])
        self.assertEqual(found, {"Docker": [4]})

    def test_skips_uncertain_common_and_dissimilar(self):
        text = level_guide()["subskills"]["C1-4"]["levels"]["2"]
        postings = [_posting(
            ("Docker", text, "uncertain"),         # 근거 확인 안 됨
            ("AWS", "AWS 운영 경험", "high"),       # 공통 기술(ALL)
        )]
        self.assertEqual(rl.posting_levels(postings), {})
        with mock.patch.object(rl, "MIN_SIMILARITY", 1.01):  # 어떤 문장도 기준을 못 넘음
            self.assertEqual(rl.posting_levels([_posting(("Docker", text, "high"))]), {})

    def test_one_level_per_posting(self):
        guide = level_guide()["subskills"]["C1-4"]["levels"]
        found = rl.posting_levels([_posting(("Docker", guide["2"], "high"), ("Docker", guide["5"], "high"))])
        self.assertEqual(found, {"Docker": [5]})


if __name__ == "__main__":
    unittest.main()
