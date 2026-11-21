"""학습 추천 화면 (Q3 왜 이 교육과정을 추천했나).

부족한 기술마다 탭을 두고 추천 과정을 카드로 보여 주며 순위를 적는다(2026-11-20). 카드는 app/course_card.py 가 그린다.
"""
import streamlit as st

from app.common import COMMON_AREA, PENDING, Ctx, load, md_escape, notice
from app.course_card import RATING_NOTE, duration_text, is_https, render_course_list
from backend import api
from backend.plan import plan_minutes, study_order

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
        first = plan[0]
        if is_https(first.get("url")):  # 할 일을 하나로 모은다: 순서의 첫 과정을 바로 연다
            took = duration_text(first.get("duration_min"))
            st.link_button(f"1번 과정부터 시작하기{' · ' + took if took else ''}", first["url"], type="primary",
                           icon=":material/play_arrow:")
        total, unknown = plan_minutes(plan)
        if total:  # 얼마나 걸릴지 알아야 계획을 세운다. 합계는 backend/plan.py 가 낸 값이다
            rest = f" (소요 시간을 모르는 과정 {unknown}개 제외)" if unknown else ""
            st.markdown(f":material/schedule: 과정 {len(plan)}개 · **모두 들으면 {duration_text(total)}**{rest}")
        st.caption("공고 수요가 높은 기술부터 놓고, 선수지식이 필요한 과정은 뒤로 미뤘습니다. 순서는 권장이며 건너뛰어도 됩니다.")


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
    languages = {c["language"] for rec in recs for c in rec["courses"] if c.get("language")}
    if languages == {"English"}:  # 한국어 화면에서 과정을 열고서야 영어임을 알게 하지 않는다
        st.info("추천 과정은 모두 **영어 강의**입니다(AWS Skill Builder). 자막·번역 지원은 과정 페이지에서 확인하세요.",
                icon=":material/translate:")
    area_of = {r["skill"]: r["area"] for r in missing}
    # 길게 늘어놓지 않고 기술마다 탭으로 넘겨 본다. 탭 순서는 공고 수요가 높은 순서다
    labels = [f"{priority}. {md_escape(rec['gap_skill'])}" for priority, rec in enumerate(recs, start=1)]
    for number, (tab, rec) in enumerate(zip(st.tabs(labels), recs)):
        d = rec["demand"]
        with tab:
            st.subheader(rec["gap_skill"], anchor=False)
            level = f" · 내 단계 {rec['user_level']}" if "user_level" in rec else ""
            # 왜 이 기술을 먼저 보라고 하는지(공고 수요와 내 단계)를 카드 위에 본문 글씨로 적는다
            st.markdown(f"**왜 이 기술인가요** · 공고 {d['of']}건 중 {d['postings']}건이 요구 · 그중 필수 {d['required_ratio']:.0%}{level}")
            if not rec["courses"] and area_of.get(rec["gap_skill"]) == COMMON_AREA:
                st.markdown(f"여러 영역에 걸친 기술이라 한 영역의 과정으로 잇지 않았습니다. "
                            f"AWS 기초·자격증 과정 연결을 준비하고 있습니다. {PENDING}")
            elif not rec["courses"]:
                st.write("이 기술을 다루는 교육과정을 찾지 못했습니다.")
            render_course_list(rec["courses"], columns=CARDS_PER_ROW, key=f"rec{number}", user_level=rec.get("user_level"))
    st.caption("'AWS Skill Builder'는 그 기술을 직접 다루는 과정, '유사 과정'은 직접 다루는지 확인하지 못한 관련 과정입니다. "
               + RATING_NOTE)


def render(ctx: Ctx):
    if ctx.gap_err:
        notice("학습 추천", ctx.gap_err, "부족한 기술마다 학습 프로그램과 추천 이유가 여기에 나옵니다.")
    else:
        _render_recommendations(ctx)
