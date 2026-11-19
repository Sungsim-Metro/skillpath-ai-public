"""학습 추천 화면 (Q3 왜 이 교육과정을 추천했나).

부족한 기술마다 추천 과정을 카드로 보여 주고 순위를 적는다(2026-11-20). 카드는 app/course_card.py 가 그린다.
"""
import streamlit as st

from app.common import COMMON_AREA, PENDING, Ctx, load, notice
from app.course_card import RATING_NOTE, render_course_list
from backend import api
from backend.plan import study_order

CARDS_PER_ROW = 3


def _render_order(recs: list[dict]) -> None:
    """먼저 들을 순서: 직접 다루는 과정만, 선수지식을 반영해 코드가 정한 순서(backend/plan.py)."""
    plan, err = load(study_order, recs)
    if err or not plan:
        return
    with st.container(border=True):
        st.subheader("먼저 들을 순서")
        for item in plan:
            level = f" · 난이도 {item['level']}" if item["level"] else ""
            st.text(f"{item['step']}. {item['title']} ({item['skill']}{level})")
        st.caption("직접 다루는 과정만 넣었습니다. 기술은 공고 수요가 높은 순서이고, 선수지식에 다른 부족 기술이 있으면 그 과정 뒤로 미룹니다.")


def _render_recommendations(ctx: Ctx):
    missing = ctx.missing
    if not missing:
        st.info("부족한 기술이 없어 추천할 과정이 없습니다.")
        return
    level_of = {r["skill"]: r["user_level"] for r in missing}
    recs, err = load(api.get_recommendations, ctx.job, [r["skill"] for r in missing], levels=level_of)
    if err:
        notice("학습 추천", err, "부족한 기술마다 학습 프로그램과 추천 이유가 여기에 나옵니다.")
        return
    _render_order(recs)
    area_of = {r["skill"]: r["area"] for r in missing}
    for priority, rec in enumerate(recs, start=1):
        d = rec["demand"]
        with st.container(border=True):
            st.subheader(f"{priority}. {rec['gap_skill']}")
            level = f" · 내 단계 {rec['user_level']}" if "user_level" in rec else ""
            st.caption(f"공고 {d['of']}건 중 {d['postings']}건이 요구 · 그중 필수 {d['required_ratio']:.0%}{level}")
            if not rec["courses"] and area_of.get(rec["gap_skill"]) == COMMON_AREA:
                st.markdown(f"여러 영역에 걸친 기술이라 한 영역의 과정으로 잇지 않았습니다. "
                            f"AWS 기초·자격증 과정 연결을 준비하고 있습니다. {PENDING}")
            elif not rec["courses"]:
                st.write("이 기술을 다루는 교육과정을 찾지 못했습니다.")
            render_course_list(rec["courses"], columns=CARDS_PER_ROW)
    st.caption("기술은 공고 수요가 높은 순서입니다. 카드의 순위는 그 기술 안에서의 추천 순서입니다(직접 다루는 과정이 먼저, "
               "그다음 내 단계에 맞는 난이도). " + RATING_NOTE)


def render(ctx: Ctx):
    if ctx.gap_err:
        notice("학습 추천", ctx.gap_err, "부족한 기술마다 학습 프로그램과 추천 이유가 여기에 나옵니다.")
    else:
        _render_recommendations(ctx)
