"""탭 1: 시장 수요 (Q1 어떤 데이터를 분석했나)."""
import pandas as pd
import streamlit as st

from app.common import ACCENT, EMPTY, Ctx, Pending, empty_metrics, md_escape, notice

def _render_demand(ctx: Ctx):
    demand, skill_options = ctx.demand, ctx.skill_options
    total = demand["total_postings"]
    c1, c2, c3 = st.columns(3)
    c1.metric("분석한 공고", f"{total}건", border=True)
    c2.metric("추출한 기술", f"{len(demand['skills'])}개", border=True)
    c3.metric("수집일", demand.get("collected") or EMPTY, border=True)
    if demand.get("note"):
        st.caption(demand["note"])

    df = pd.DataFrame(
        [{"기술": s["skill"], "필수": s["required"], "우대": s["preferred"]} for s in demand["skills"]]
    )
    st.subheader("이 직무가 요구하는 기술")
    st.bar_chart(df, x="기술", y=["필수", "우대"], horizontal=True, sort=False,
                 color=[ACCENT, "#B9CBEA"], height=380, x_label="", y_label="요구한 공고 수 (건)")

    st.subheader("근거 보기")
    pick = st.selectbox("기술을 고르면 공고 원문 근거를 보여줍니다", skill_options, key="evidence_skill")
    s = next(x for x in demand["skills"] if x["skill"] == pick)
    st.write(f"**{pick}**: 공고 {total}건 중 {s['postings']}건 (필수 {s['required']}건, 우대 {s['preferred']}건)")
    for ev in s["evidence_samples"]:
        # 공고 상세는 화면에 옮기지 않고 원본 링크로 보낸다. 링크가 없는 공고(수동 수집)는 id 만 보인다.
        source = f"[공고 원문 열기 ↗]({ev['url']}) `{ev['posting_id']}`" if ev.get("url") else f"`{ev['posting_id']}`"
        st.markdown(f"> {md_escape(ev['evidence'])}  \n> {source}")




def render(ctx: Ctx):
    if ctx.demand_err:
        if isinstance(ctx.demand_err, Pending):
            empty_metrics(["분석한 공고", "추출한 기술", "수집일"])
        notice("이 직무가 요구하는 기술", ctx.demand_err, "채용공고에서 뽑은 기술 순위와 근거 문장이 여기에 나옵니다.")
    else:
        _render_demand(ctx)
