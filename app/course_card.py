"""추천 과정 카드. 학습 상담과 학습 추천 화면이 함께 쓴다(2026-11-20).

- 순위는 받은 목록의 순서에 번호만 붙인다. 순서는 backend 가 정한다(직접 다루는 과정이 먼저, 그 안에서는 내 단계에 맞는 난이도가 먼저).
- 배지: 부족한 기술을 직접 다루는 과정은 "AWS Skill Builder", 직접 다루는지 확인하지 못한 과정(match 가 uncertain)은 "유사 과정".
  유사 과정을 확인된 과정처럼 보이게 하지 않는다(원칙 4).
- 평점은 AWS Skill Builder 과정 페이지의 별점 평균과 평가 수이고(계약 ③ 의 선택 필드), 있을 때만 보여 준다. 누르면 그 과정 페이지가 열린다.
  추천 순서에는 쓰지 않는다.
- 카드 높이를 맞추고 추천 이유는 접어 둔다. 과정이 여러 기술에 걸치면 기술마다 탭으로 나눈다.
- 과정 데이터의 글자는 마크다운 기호를 지운 뒤(md_escape) 그리거나 `st.text` 로 그린다. 그대로 해석되면 링크나 이미지가 그려질 수 있다.
- 과정 원문 문장은 보여 주지 않는다(원칙 3).
- 담기: 카드의 버튼으로 과정을 담아 메뉴의 '담은 교육과정' 화면(app/tabs/saved.py)에서 모아 본다.
  로그인했으면 프로필에 저장하고(backend.chat_profiles), 비회원이면 이 브라우저 세션에만 둔다.
"""
from urllib.parse import urlsplit

import streamlit as st

from app.common import md_escape
from backend import chat_profiles

RATING_NOTE = "★ 평점은 AWS Skill Builder 과정 페이지의 별점 평균과 평가 수입니다(수집한 날 기준). 추천 순서에는 쓰지 않습니다."
CARD_HEIGHT = 380  # 카드 한 장의 높이(px). 넘치는 내용은 카드 안에서 스크롤한다
SAVE_FAILED = "담은 교육과정을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
CERTAIN_BADGE = ":green-badge[:material/verified: AWS Skill Builder]"
SIMILAR_BADGE = ":orange-badge[:material/travel_explore: 유사 과정]"


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


def _toggle_saved(course: dict) -> None:
    """담기 버튼. 담겨 있으면 빼고 없으면 담는다. 로그인했으면 프로필에 저장한 뒤의 목록을 화면에 쓴다."""
    saved = dict(st.session_state.get("saved_courses") or {})
    url = course["url"]
    item = {"title": course["title"], "url": url, "level": course.get("level") or ""}
    session = st.session_state.get("profile_session")
    try:
        if session is not None:
            items = chat_profiles.update_saved_courses(
                session, add=[] if url in saved else [item], remove_urls=[url] if url in saved else [])
            saved = {c["url"]: c for c in items}
        elif url in saved:
            del saved[url]
        elif len(saved) >= chat_profiles.SAVED_COURSE_LIMIT:
            raise chat_profiles.SavedCourseLimit()
        else:
            saved[url] = item
    except chat_profiles.SavedCourseLimit as exc:
        st.toast(str(exc))
        return
    except Exception:  # 예외에 프로필 내용이 들어 있을 수 있어 고정 문구만 보여 준다
        st.toast(SAVE_FAILED)
        return
    st.session_state.saved_courses = saved


def sync_saved(session) -> None:
    """로그인하면 프로필에 담아 둔 과정을 불러온다. 비회원일 때 담은 과정은 그때 프로필에 합친다.

    로그인 상태가 바뀔 때 한 번만 저장소를 읽는다. 로그아웃하면 세션이 비워져 목록도 사라진다.
    """
    owner = session.nickname if session is not None else None
    if st.session_state.get("saved_owner") == owner:
        return
    st.session_state.saved_owner = owner
    if session is None:
        return
    guest = list((st.session_state.get("saved_courses") or {}).values())
    try:
        try:
            items = (chat_profiles.update_saved_courses(session, add=guest) if guest
                     else chat_profiles.load_saved_courses(session))
        except chat_profiles.SavedCourseLimit:  # 합치면 한도를 넘으면 프로필에 있던 것만 보여 준다
            items = chat_profiles.load_saved_courses(session)
    except Exception:
        return  # 읽지 못하면 지금 화면의 목록을 그대로 둔다
    st.session_state.saved_courses = {c["url"]: c for c in items}


def render_course_card(course: dict, *, rank: int | None = None, key: str = "") -> None:
    match = course.get("match")  # '담은 교육과정' 화면의 카드에는 없다(어느 기술의 추천인지와 따로 담는다)
    linked = _https(course.get("url"))
    with st.container(border=True, height=CARD_HEIGHT):
        badges = [f":blue-badge[{rank}위]"] if rank else []
        if match:
            badges.append(SIMILAR_BADGE if match == "uncertain" else CERTAIN_BADGE)
        if course.get("level"):
            badges.append(f":gray-badge[난이도 {md_escape(course['level'])}]")
        if badges:
            st.markdown(" ".join(badges))
        st.markdown(f"##### {md_escape(course['title'])}")  # 제목은 크게, 아래 정보는 작고 옅게
        details = [course["gap_skill"]] if course.get("gap_skill") else []
        if course.get("prerequisites"):
            details.append(f"선수지식 {course['prerequisites']}")
        if details:
            st.caption(md_escape(" · ".join(details)))
        rating = rating_text(course)
        if rating and linked:
            st.link_button(rating, course["url"], type="tertiary", icon=":material/open_in_new:",
                           help="AWS Skill Builder 과정 페이지에서 평점을 확인합니다.")
        elif rating:
            st.caption(rating)
        if course.get("reason"):
            with st.expander("추천 이유 더보기"):
                st.text(course["reason"])
        if linked:
            st.link_button("교육과정 열기", course["url"], icon=":material/open_in_new:", width="stretch")
        if linked and key:  # 같은 과정이 여러 곳에 나오므로 그리는 쪽이 준 key 로 버튼을 구분한다
            kept = course["url"] in (st.session_state.get("saved_courses") or {})
            st.button("담아 둠" if kept else "담기", key=f"keep_{key}", on_click=_toggle_saved, args=(course,),
                      icon=":material/bookmark_added:" if kept else ":material/bookmark_add:", width="stretch",
                      type="tertiary", help="담은 과정은 메뉴의 '담은 교육과정'에서 모아 봅니다.")


def _render_cards(courses: list[dict], columns: int, key: str) -> None:
    slots = st.columns(min(columns, len(courses))) if columns > 1 and len(courses) > 1 else None
    for index, course in enumerate(courses, start=1):
        card_key = f"{key}_{index}" if key else ""
        if slots:
            with slots[(index - 1) % len(slots)]:
                render_course_card(course, rank=index, key=card_key)
        else:
            render_course_card(course, rank=index, key=card_key)


def render_course_list(courses: list[dict], *, columns: int = 1, key: str = "") -> None:
    """과정을 받은 순서대로 카드로 그리고 기술별로 1위부터 번호를 붙인다. columns 가 2 이상이면 옆으로 나란히 놓는다.

    과정이 여러 기술에 걸쳐 있으면 길게 늘어놓지 않고 기술마다 탭으로 나눈다.
    key 는 이 목록을 화면에서 구분하는 이름이다. 주면 카드마다 담기 버튼이 생긴다.
    """
    groups: dict[str, list[dict]] = {}
    for course in courses:
        groups.setdefault(course.get("gap_skill", ""), []).append(course)
    if len(groups) <= 1:
        _render_cards(courses, columns, key)
        return
    for number, (tab, group) in enumerate(zip(st.tabs([md_escape(skill) or "기타" for skill in groups]), groups.values())):
        with tab:
            _render_cards(group, columns, f"{key}_g{number}" if key else "")
