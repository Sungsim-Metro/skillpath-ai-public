"""교육과정 → 섹션 단위 청크 (#14). 임베딩할 글을 만든다.

과정 1개를 제목, 학습 목표, 설명으로 나눈다(과정당 최대 3개). 필드마다 따로 임베딩하는 이유:
- 제목에 기술 이름이 있는 과정과 설명에만 스치는 과정을 점수에서 구분하기 위해서다.
- 필드가 서로 섞이지 않아 청크가 너무 크거나 작아지지 않는다(학습 목표는 평균 270자, 설명은 평균 680자).
빈 필드는 청크를 만들지 않는다(학습 목표가 없는 과정은 제목과 설명만 남는다).
"""
from __future__ import annotations

from pipeline.search import _courses

DESCRIPTION_MAX = 800  # ponytail: 긴 설명은 앞 800자만 쓴다. 뒷부분이 중요해지면 문단 단위로 나눈다
FIELDS = ("title", "objectives", "description")


def chunks_of(course: dict) -> list[dict]:
    """과정 1개의 청크 목록 [{course_id, field, text}]."""
    title = course["title"].strip()
    services = course["aws_services"].strip()
    texts = {
        "title": f"{title}. {services}" if services else title,
        "objectives": course["objectives"].strip(),
        "description": course["description"].strip()[:DESCRIPTION_MAX],
    }
    return [{"course_id": course["course_id"], "field": f, "text": texts[f]} for f in FIELDS if texts[f]]


def all_chunks() -> list[dict]:
    """추천할 수 있는 과정(종료 예정이 아니고 학습 목표가 있는 과정)의 청크 전부."""
    from pipeline.search import _usable

    return [ch for c in _courses().values() if _usable(c) for ch in chunks_of(c)]
