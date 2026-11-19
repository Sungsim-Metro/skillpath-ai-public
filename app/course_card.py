"""추천 과정 카드. 학습 상담과 학습 추천 화면이 함께 쓴다(2026-11-20).

- 순위는 받은 목록의 순서에 번호만 붙인다. 순서는 backend 가 정한다(직접 다루는 과정이 먼저, 그 안에서는 내 단계에 맞는 난이도가 먼저).
- 평점은 AWS Skill Builder 과정 페이지의 별점 평균과 평가 수이고(계약 ③ 의 선택 필드), 있을 때만 한 줄로 보여 준다.
  추천 순서에는 쓰지 않는다.
- 과정 데이터의 글자는 `st.text` 로 그린다. 마크다운으로 해석되면 링크나 이미지가 그려질 수 있다.
- 과정 원문 문장은 보여 주지 않는다(원칙 3).
"""
from urllib.parse import urlsplit

import streamlit as st

RATING_NOTE = "★ 평점은 AWS Skill Builder 과정 페이지의 별점 평균과 평가 수입니다(수집한 날 기준). 추천 순서에는 쓰지 않습니다."


def rating_text(course: dict) -> str:
    """'★ 4.6 · 평가 1,280개'. 평점이 없으면 빈 글자."""
    avg, count = course.get("rating_avg"), course.get("rating_count")
    if not isinstance(avg, (int, float)) or not isinstance(count, int) or isinstance(avg, bool) or count <= 0:
        return ""
    return f"★ {avg:.1f} · 평가 {count:,}개"


def _https(url: str) -> bool:
    try:
        target = urlsplit(url or "")
    except ValueError:
        return False
    return target.scheme == "https" and bool(target.hostname)


def render_course_card(course: dict, *, rank: int | None = None) -> None:
    uncertain = course["match"] == "uncertain"
    with st.container(border=True):
        badges = [f":blue-badge[{rank}위]"] if rank else []
        badges.append(":orange-badge[불확실]" if uncertain else ":green-badge[직접 다루는 과정]")
        st.markdown(" ".join(badges))
        st.text(course["title"])
        details = [course["gap_skill"]] if course.get("gap_skill") else []
        details.append("매칭 불확실" if uncertain else "매칭 확실")
        if course.get("level"):
            details.append(f"난이도 {course['level']}")
        if course.get("prerequisites"):
            details.append(f"선수지식 {course['prerequisites']}")
        st.text(" · ".join(details))
        rating = rating_text(course)
        if rating:
            st.text(rating)
        if course.get("reason"):
            st.text(course["reason"])
        if _https(course.get("url")):
            st.link_button("교육과정 열기", course["url"], icon=":material/open_in_new:")


def render_course_list(courses: list[dict], *, columns: int = 1) -> None:
    """과정을 받은 순서대로 카드로 그리고 기술별로 1위부터 번호를 붙인다. columns 가 2 이상이면 옆으로 나란히 놓는다."""
    ranks: dict[str, int] = {}
    slots = st.columns(min(columns, len(courses))) if columns > 1 and len(courses) > 1 else None
    for index, course in enumerate(courses):
        skill = course.get("gap_skill", "")
        ranks[skill] = ranks.get(skill, 0) + 1
        if slots:
            with slots[index % len(slots)]:
                render_course_card(course, rank=ranks[skill])
        else:
            render_course_card(course, rank=ranks[skill])
