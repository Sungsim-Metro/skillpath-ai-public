"""근거 문장 검사 (최소형. #16 에서 보강).

evidence 가 공고 text 에 그대로 있는지 코드로 확인한다.
공백·줄바꿈 차이만 무시하고, 비슷한 문장 찾기는 하지 않는다 (확신이 없으면 불확실).
"""
import re


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def evidence_in_text(evidence: str, text: str) -> bool:
    ev = _squash(evidence)
    return bool(ev) and ev in _squash(text)
