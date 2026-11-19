"""오픈 데이터셋 후보 선정과 직무 판정 검사. 실행: python -m unittest pipeline.test_wanted_dataset"""
import unittest

from pipeline import wanted_dataset as wd

BODY = "주요 업무\n- Kubernetes 기반 서비스 인프라를 구축하고 운영합니다. " + "가" * 200


def row(job_id, title, text=BODY, source="wanted"):
    return {"source": source, "job_id": job_id, "company": "회사", "title": title, "location": "서울",
            "experience_level": "경력무관", "raw_text": text}


class WantedDatasetTest(unittest.TestCase):
    def test_candidates_follow_the_rules(self):
        rows = [
            row("3", "DevOps Engineer"),
            row("3", "DevOps Engineer"),                           # 같은 공고 번호
            row("4", "Cloud Engineer"),                            # 본문이 같은 공고
            row("5", "AI Engineer", BODY + " 문의 a.b@example.com 02-123-4567"),
            row("6", "AI Tutor - German", BODY + "6"),             # 교육·강사
            row("7", "백엔드 개발자", BODY + "7"),                 # 직무 계열 단어 없음
            row("8", "MLOps Engineer", "짧은 본문"),
            row("9", "SRE", BODY + "9", source="saramin"),
        ]
        found = wd.candidates(rows)
        self.assertEqual([p["id"] for p in found], ["hf-wanted-3", "hf-wanted-5"])
        self.assertEqual(found[0]["title_terms"], {"cloud": ["devops"], "ai": []})
        self.assertNotIn("example.com", found[1]["text"])          # 이메일과 전화번호는 가린다
        self.assertNotIn("4567", found[1]["text"])
        self.assertIsNone(found[0]["posted_at"])                   # 페이지를 조회하지 않으므로 게시일은 없다

    def test_made_up_evidence_is_lowered_to_uncertain(self):
        text = "Kubernetes 기반  서비스 인프라를\n구축하고 운영합니다."
        ok = {"cloud": {"fit": "yes", "evidence": "Kubernetes 기반 서비스 인프라를 구축하고 운영합니다."},
              "ai": {"fit": "no", "evidence": ""}}
        self.assertEqual(wd.check_review(ok, text)["cloud"]["fit"], "yes")   # 공백 차이는 같은 문장으로 본다
        fake = {**ok, "cloud": {"fit": "yes", "evidence": "AWS 인프라를 설계합니다."}}
        self.assertEqual(wd.check_review(fake, text)["cloud"], {"fit": "uncertain", "evidence": ""})
        for bad in ({"cloud": {"fit": "maybe", "evidence": ""}, "ai": ok["ai"]}, {"cloud": ok["cloud"]}):
            with self.assertRaises(ValueError):
                wd.check_review(bad, text)


if __name__ == "__main__":
    unittest.main()
