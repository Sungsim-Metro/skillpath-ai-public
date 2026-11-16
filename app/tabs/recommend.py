"""탭 3: 학습 추천 (Q3 왜 이 교육과정을 추천했나)."""
import streamlit as st

from app.common import COMMON_AREA, PENDING, UNCERTAIN, Ctx, load, md_escape, notice
from backend import api

def _render_recommendations(ctx: Ctx):
    missing = ctx.missing
    if not missing:
        st.info("부족한 기술이 없어 추천할 과정이 없습니다.")
        return
    recs, err = load(api.get_recommendations, ctx.job, [r["skill"] for r in missing])
    if err:
        notice("학습 추천", err, "부족한 기술마다 AWS 교육과정과 추천 이유가 여기에 나옵니다.")
        return
    area_of = {r["skill"]: r["area"] for r in missing}
    for rec in recs:
        d = rec["demand"]
        with st.container(border=True):
            st.subheader(rec["gap_skill"])
            st.caption(f"공고 {d['of']}건 중 {d['postings']}건이 요구 · 그중 필수 {d['required_ratio']:.0%}")
            if not rec["courses"] and area_of.get(rec["gap_skill"]) == COMMON_AREA:
                st.markdown(f"여러 영역에 걸친 기술이라 한 영역의 과정으로 잇지 않았습니다. "
                            f"AWS 기초·자격증 과정 연결을 준비하고 있습니다. {PENDING}")
            elif not rec["courses"]:
                st.write("이 기술을 다루는 교육과정을 찾지 못했습니다.")
            for i, c in enumerate(rec["courses"]):
                if i:
                    st.divider()
                info, action = st.columns([3, 1], vertical_alignment="center")
                with info:
                    badge = f" {UNCERTAIN}" if c["match"] == "uncertain" else ""
                    st.markdown(f"**{md_escape(c['title'])}**{badge}")
                    st.caption(f"난이도 {c['level']} · 선수지식 {c['prerequisites']}")
                    st.write(c["reason"])
                with action:
                    st.link_button("Skill Builder 열기", c["url"], icon=":material/open_in_new:",
                                   type="primary", width="stretch")




def render(ctx: Ctx):
    if ctx.gap_err:
        notice("학습 추천", ctx.gap_err, "부족한 기술마다 AWS 교육과정과 추천 이유가 여기에 나옵니다.")
    else:
        _render_recommendations(ctx)
