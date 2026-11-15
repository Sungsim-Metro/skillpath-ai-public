"""공고 본문의 칸(section) 찾기와 칸 기준 가중치 (② skills[].section).

칸 제목 모양은 수업 4주차 실습의 방식에 맞춘다(코드는 따로 썼다): 줄 앞(공백 허용)이 `[제목]` 이면 칸 제목.
① text 는 칸마다 `[주요업무]` 처럼 제목을 한 줄에 단독으로 쓰고 다음 줄부터 본문을 쓴다.

section 값
- qualification(자격요건), duty(주요업무), preference(우대사항): 표에 있는 칸 제목
- "other": 표에 없는 칸 제목, 또는 첫 칸 제목 앞의 글(실습의 "기타")
- None: 칸 제목이 하나도 없는 공고, 또는 근거 문장을 본문에서 찾지 못함
- EXCLUDED(내부 값): 혜택·회사소개 같은 칸. 이 칸에서 나온 기술은 추출 결과에서 코드가 버린다

칸 제목 비교는 공백과 `·:/()-_.` 만 빼고 정확히 같은지 본다. 비슷하다고 추측해서 맞추지 않는다.

수요 가중치(2026-11-15 팀 결정, 5주차 실습 값): 칸이 있으면 칸 기준, 칸이 없거나 모르는 칸이면 type 기준.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

OTHER = "other"
EXCLUDED = "excluded"  # 내부 값. ② 에는 나가지 않는다

SECTION_NAMES = {
    "qualification": ("자격요건", "지원자격", "필수요건", "필수자격", "자격조건", "요구사항"),
    "duty": ("주요업무", "담당업무", "업무내용", "하는일"),
    "preference": ("우대사항", "우대조건", "우대요건"),
    EXCLUDED: ("회사소개", "혜택및복지", "복리후생", "채용절차", "근무조건", "근무환경"),
}
SECTION_WEIGHT = {"qualification": 1.0, "duty": 0.7, "preference": 0.5}
TYPE_WEIGHT = {"required": 1.0, "preferred": 0.5}

_HEADING = re.compile(r"\s*\[(.+?)\]")


def _key(name: str) -> str:
    return re.sub(r"[\s·:/()\-_.]", "", name)


_LOOKUP = {_key(n): value for value, names in SECTION_NAMES.items() for n in names}


def heading_value(name: str) -> str:
    """칸 제목 → section 값. 표에 없으면 other (신규 칸 제목 후보로 로그를 남긴다)."""
    value = _LOOKUP.get(_key(name))
    if value is None:
        logger.info("표에 없는 칸 제목: [%s] → other", name)
        return OTHER
    return value


def headings(text: str) -> list[tuple[int, str, str]]:
    """본문의 칸 제목 목록: (그 줄이 시작하는 글자 위치, 제목 원문, section 값)."""
    out, pos = [], 0
    for line in text.splitlines(keepends=True):
        m = _HEADING.match(line)
        if m:
            out.append((pos, m.group(1).strip(), heading_value(m.group(1))))
        pos += len(line)
    return out


def _find(text: str, evidence: str) -> int:
    """근거 문장이 본문에서 처음 나오는 위치. 공백·줄바꿈 차이만 허용한다(ai.evidence 와 같은 기준). 없으면 -1."""
    i = text.find(evidence)
    if i >= 0:
        return i
    words = evidence.split()
    if not words:
        return -1
    m = re.search(r"\s+".join(map(re.escape, words)), text)
    return m.start() if m else -1


def find_section(text: str, evidence: str) -> Optional[str]:
    """근거 문장이 들어 있는 칸의 section 값. 같은 문장이 두 번 나오면 처음 나온 곳 기준."""
    hs = headings(text)
    if not hs:
        return None
    i = _find(text, evidence)
    if i < 0:
        return None
    before = [value for start, _, value in hs if start <= i]
    return before[-1] if before else OTHER


def weight(type_: str, section: Optional[str]) -> float:
    """수요 가중치. 칸이 있으면 칸 기준, 칸이 없거나 모르는 칸이면 type 기준."""
    if section in SECTION_WEIGHT:
        return SECTION_WEIGHT[section]
    return TYPE_WEIGHT[type_]
