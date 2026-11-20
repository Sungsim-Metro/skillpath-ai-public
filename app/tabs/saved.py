"""담은 교육과정 화면 (2026-11-20). 추천 카드의 '담기'로 모은 과정을 한곳에서 본다.

로그인한 사용자의 목록은 프로필에 저장되고(backend.chat_profiles), 비회원의 목록은 이번 접속 동안만 남는다.
목록은 app/course_card.py 의 sync_saved 와 담기 버튼이 세션에 맞춰 둔다. 평점은 backend.api.get_saved_course_cards 가
과정 표에서 붙이고, 화면은 담은 순서대로 그리기만 한다.
"""
import streamlit as st

from app import ui
from app.common import Ctx, load
from app.course_card import RATING_NOTE, render_course_card
from backend import api

CARDS_PER_ROW = 3


def render(ctx: Ctx) -> None:
    saved = list((st.session_state.get("saved_courses") or {}).values())
    if st.session_state.get("profile_session") is not None:
        st.caption("담은 과정은 로그인한 프로필에 저장됩니다. 다시 로그인해도 그대로 보입니다.")
    else:
        st.caption("비회원은 이번 접속 동안만 보관합니다. 로그인하면 지금 담은 과정이 프로필에 저장됩니다.")
    if not saved:
        st.info("아직 담은 교육과정이 없습니다. 학습 상담이나 학습 추천의 카드에서 '담기'를 누르면 여기에 모입니다.")
        ui.next_step("recommend", "학습 추천 보기")
        return
    cards, err = load(api.get_saved_course_cards, saved)
    if err or not cards:
        cards = saved  # 평점을 붙이지 못해도 담은 과정은 보여 준다
    st.caption(f"{len(cards)}개 · {RATING_NOTE}")
    slots = st.columns(CARDS_PER_ROW)
    for number, course in enumerate(cards):
        with slots[number % CARDS_PER_ROW]:
            render_course_card(course, key=f"saved_{number}")
