"""탭 1: 시장 수요 (Q1 어떤 데이터를 분석했나)."""
import pandas as pd
import streamlit as st

from app.common import ACCENT, EMPTY, Ctx, Pending, empty_metrics, md_escape, notice

CHART_TOP = 15  # 막대그래프에 그리는 기술 수


def _render_demand(ctx: Ctx):
    demand, skill_options = ctx.demand, ctx.skill_options
    total = demand["total_postings"]
    c1, c2, c3 = st.columns(3)
    c1.metric("분석한 공고", f"{total}건", border=True)
    c2.metric("추출한 기술", f"{len(demand['skills'])}개", border=True)
    c3.metric("수집일", demand.get("collected") or EMPTY, border=True)
    if demand.get("note"):
        st.caption(demand["note"])
    if not skill_options:
        st.info("이 직무의 공고에서 추출한 기술이 없습니다.")
        return

    rows = [{"기술": s["skill"], "필수": s["required"], "우대": s["preferred"]} for s in demand["skills"]]
    shown = rows[:CHART_TOP]  # 백엔드가 공고 수 순으로 준 목록의 앞쪽만 그린다(기술이 100개가 넘으면 막대가 빼곡해 읽을 수 없다)
    # 근거를 차트보다 먼저 둔다. 부족 역량의 '공고 근거 보기'로 넘어오면 그 기술이 골라져 있다(app/ui.py 의 go)
    with st.container(border=True):
        st.subheader("이 기술이 왜 필요한가요")
        if st.session_state.get("evidence_skill") not in skill_options:  # 직무가 바뀌어 목록에 없는 기술이면 비운다
            st.session_state.pop("evidence_skill", None)
        pick = st.selectbox("기술을 고르면 그 기술을 요구한 공고 문장을 보여 줍니다", skill_options, key="evidence_skill")
        s = next(x for x in demand["skills"] if x["skill"] == pick)
        st.write(f"**{pick}**: 공고 {total}건 중 {s['postings']}건 (필수 {s['required']}건, 우대 {s['preferred']}건)")
        for ev in s["evidence_samples"]:
            # 공고 상세는 화면에 옮기지 않고 원본 링크로 보낸다. 링크가 없는 공고(수동 수집)는 id 만 보인다.
            source = f"[공고 원문 열기 ↗]({ev['url']}) `{ev['posting_id']}`" if ev.get("url") else f"`{ev['posting_id']}`"
            st.markdown(f"> {md_escape(ev['evidence'])}  \n> {source}")

    st.subheader("이 직무가 요구하는 기술")
    if len(rows) > len(shown):
        st.caption(f"요구한 공고가 많은 상위 {len(shown)}개만 그렸습니다. 전체 {len(rows)}개는 아래 표에서 봅니다.")
    st.bar_chart(pd.DataFrame(shown), x="기술", y=["필수", "우대"], horizontal=True, sort=False,
                 color=[ACCENT, "#B9CBEA"], height=max(240, 26 * len(shown)), x_label="", y_label="요구한 공고 수 (건)")
    if len(rows) > len(shown):
        with st.expander(f"전체 {len(rows)}개 기술 보기"):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render(ctx: Ctx):
    if ctx.demand_err:
        if isinstance(ctx.demand_err, Pending):
            empty_metrics(["분석한 공고", "추출한 기술", "수집일"])
        notice("이 직무가 요구하는 기술", ctx.demand_err, "채용공고에서 뽑은 기술 순위와 근거 문장이 여기에 나옵니다.")
    else:
        _render_demand(ctx)
