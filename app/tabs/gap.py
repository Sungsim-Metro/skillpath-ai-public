"""탭 2: 부족 역량 (Q2 어떤 근거로 필요하다 판단했나)."""
import altair as alt
import pandas as pd
import streamlit as st

from app.tabs.connect import render_connect
from app.common import (
    ACCENT,
    ACCENT_LIGHT,
    ACCENT_LINE,
    MARKET_DEMAND,
    MARKET_ROWS,
    MARKET_SUPPLY,
    Ctx,
    Pending,
    empty_metrics,
    load,
    notice,
)
from app.tabs.radar import render_radar
from backend import api

def render_area_gap(ctx: Ctx):
    """능력 범주별 Gap: 영역마다 공고 수요(옅은 막대) 위에 내가 가진 몫(진한 막대)을 겹친다."""
    rows, err = load(api.get_area_gap, ctx.job, ctx.levels)
    if err:
        notice("능력 범주별 Gap", err)
        return
    shown = [r for r in rows if r["demand"] > 0]
    empty = [r["area"] for r in rows if r["demand"] == 0 and r["area_id"] != "UNCLASSIFIED"]
    df = pd.DataFrame([
        {
            "영역": r["area"],
            "수요": r["demand"],
            "보유": r["owned"],
            "표시": f"{r['owned']} / {r['demand']} ({r['coverage']:.0%})",
            "기술": ", ".join(r["skills"]),
            "부족": ", ".join(r["missing_skills"]) or "없음",
        }
        for r in shown
    ])
    tooltip = [alt.Tooltip("영역:N"), alt.Tooltip("수요:Q", title="공고 수요"), alt.Tooltip("보유:Q", title="내 보유"),
               alt.Tooltip("기술:N", title="이 영역의 기술"), alt.Tooltip("부족:N", title="부족한 기술")]
    base = alt.Chart(df).encode(y=alt.Y("영역:N", sort=list(df["영역"]), title=None, axis=alt.Axis(labelLimit=220)))
    x_demand = alt.X("수요:Q", title="요구 건수 (영역 안 기술별 공고 수의 합)", axis=alt.Axis(tickCount=6),
                     scale=alt.Scale(domain=[0, df["수요"].max() * 1.3]))
    chart = alt.layer(
        base.mark_bar(color=ACCENT_LIGHT, stroke=ACCENT_LINE, height=24).encode(x=x_demand, tooltip=tooltip),
        base.mark_bar(color=ACCENT, height=24).encode(x=alt.X("보유:Q"), tooltip=tooltip),
        base.mark_text(align="left", dx=8, color="#566070").encode(x=alt.X("수요:Q"), text="표시:N"),
    ).properties(height=44 * len(df) + 50)
    st.altair_chart(chart, width="stretch")
    st.caption(
        "옅은 막대는 공고가 그 영역의 기술을 요구한 건수, 진한 막대는 그중 내가 채운 몫입니다. "
        "기술마다 필수로 요구한 공고는 기준 단계를 넘으면, 우대로 요구한 공고는 내 단계/5만큼 채운 것으로 셉니다. "
        "비어 있는 만큼이 Gap입니다. 여러 영역에 걸치는 기술(AWS 등)은 공통, 사전에 없는 기술은 미분류로 셉니다."
        + (f" 이 데이터에서 수요가 없는 영역: {', '.join(empty)}." if empty else "")
    )


def render_market_gap(ctx: Ctx):
    """채용 시장 Gap: 차트 ① 수요 vs 공급 비중, 차트 ② 기술별 Gap(%p). 계산은 backend 가 한다."""
    market, err = load(api.get_market_gap, ctx.job)
    if err:
        notice("채용 시장 Gap", err, "공고가 원하는 기술과 교육이 다루는 기술의 차이가 여기에 나옵니다.")
        return
    rows = market["rows"]
    if len(rows) > MARKET_ROWS:  # Gap 이 큰 쪽과 작은 쪽을 함께 보여준다
        rows = rows[: MARKET_ROWS - 4] + rows[-4:]
    df = pd.DataFrame([
        {"기술": r["skill"], "수요(공고)": r["demand_pct"], "공급(교과목)": r["supply_pct"], "Gap": r["gap_pp"],
         "표시": f"{r['gap_pp']:+.1f}", "내 보유": "보유" if r["skill"] in ctx.owned else "-"}
        for r in rows
    ])
    order = list(df["기술"])
    left, right = st.columns(2)
    with left:
        st.markdown("**① 수요 vs 공급 비중 (%)**")
        long = df.melt(id_vars="기술", value_vars=["수요(공고)", "공급(교과목)"], var_name="구분", value_name="비중")
        st.altair_chart(alt.Chart(long).mark_bar().encode(
            y=alt.Y("기술:N", sort=order, title=None),
            x=alt.X("비중:Q", title="비중 (%)"),
            yOffset=alt.YOffset("구분:N", sort=["수요(공고)", "공급(교과목)"]),
            color=alt.Color("구분:N", scale=alt.Scale(domain=["수요(공고)", "공급(교과목)"],
                                                      range=[MARKET_DEMAND, MARKET_SUPPLY]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["기술", "구분", "비중"]).properties(height=30 * len(df) + 70), width="stretch")
    with right:
        st.markdown("**② 기술별 Gap (%p)**")
        base = alt.Chart(df).encode(y=alt.Y("기술:N", sort=order, title=None))
        pad = max(abs(df["Gap"].min()), abs(df["Gap"].max())) * 1.25
        bars = base.mark_bar().encode(
            x=alt.X("Gap:Q", title="Gap (%p) = 수요 비중 − 공급 비중", scale=alt.Scale(domain=[-pad, pad])),
            color=alt.condition("datum.Gap >= 0", alt.value(MARKET_DEMAND), alt.value(MARKET_SUPPLY)),
            tooltip=["기술", "수요(공고)", "공급(교과목)", "Gap", "내 보유"])
        plus = base.transform_filter("datum.Gap >= 0").mark_text(align="left", dx=4, color="#566070").encode(
            x="Gap:Q", text="표시:N")
        minus = base.transform_filter("datum.Gap < 0").mark_text(align="right", dx=-4, color="#566070").encode(
            x="Gap:Q", text="표시:N")
        st.altair_chart((bars + plus + minus).properties(height=30 * len(df) + 70), width="stretch")
    by_section, by_type, sup = market["weights"]["section"], market["weights"]["type"], market["supply"]
    st.caption(
        "+면 시장은 원하는데 교육이 덜 다루는 기술, −면 교육이 상대적으로 많이 다루는 기술입니다. "
        f"수요 가중치의 팀 기준은 자격요건 {by_section['qualification']}, 주요업무 {by_section['duty']}, "
        f"우대사항 {by_section['preference']}입니다. 지금 집계에는 칸별 건수가 없어 "
        f"필수 {by_type['required']}, 우대 {by_type['preferred']}로 계산했습니다. "
        f"공급은 교과목 {sup['total_courses']}개(출처 {sup['source']}) 기준입니다."
        + (f" {sup['note']}" if sup.get("note") else "")
    )
    with st.expander("전체 표 보기"):
        st.dataframe(
            pd.DataFrame([
                {"기술": r["skill"], "수요 비중(%)": r["demand_pct"], "공급 비중(%)": r["supply_pct"],
                 "Gap(%p)": r["gap_pp"], "내 보유": "보유" if r["skill"] in ctx.owned else "-"}
                for r in market["rows"]
            ]),
            hide_index=True,
            width="stretch",
        )


def _render_gap(ctx: Ctx):
    demand, gap_rows, missing = ctx.demand, ctx.gap_rows, ctx.missing
    have = [r for r in gap_rows if r["owned"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석한 공고", f"{demand['total_postings']}건", border=True)
    c2.metric("주요 요구 기술", f"{len(gap_rows)}개", help="공고의 20% 이상이 요구한 기술", border=True)
    c3.metric("그중 보유", f"{len(have)}개", help="내 단계가 기준 단계 이상인 기술", border=True)
    c4.metric("부족", f"{len(missing)}개", border=True)
    guessed = [r for r in gap_rows if r["required_level_confidence"] == "uncertain"]
    if guessed:
        st.warning(
            f"주요 요구 기술 {len(gap_rows)}개 중 {len(guessed)}개는 아직 공고로 기준 단계를 정하지 못해 "
            f"팀이 정한 추정값(3단계)으로 보유·부족을 나눴습니다. 이 기술들의 판정은 참고용이며, "
            "표에 '불확실'로 표시합니다.",
            icon=":material/info:",
        )

    with st.container(border=True):
        st.subheader("능력 범주별 Gap")
        render_area_gap(ctx)

    with st.container(border=True):
        st.subheader("영역별 역량: 지금과 추천 과정을 마친 뒤")
        render_radar(ctx)

    with st.container(border=True):
        st.subheader("채용 시장 Gap")
        render_market_gap(ctx)

    with st.container(border=True):
        if not missing:
            st.success("주요 요구 기술을 모두 보유했습니다.")
        else:
            st.subheader(f"부족한 기술 {len(missing)}개 (수요 높은 순)")
            st.dataframe(
                pd.DataFrame([
                    {
                        "기술": r["skill"],
                        "영역": r["area"],
                        "요구 공고": f"{r['postings']} / {r['of']}",
                        "요구 비율": r["ratio"],
                        "그중 필수": r["required_ratio"],
                        "내 단계": r["user_level"],
                        "기준 단계": f"{r['required_level']}" + (" (불확실)" if r["required_level_confidence"] == "uncertain" else ""),
                        "충족도": r["satisfaction"],
                    }
                    for r in missing
                ]),
                hide_index=True,
                width="stretch",
                column_config={
                    "요구 비율": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                    "그중 필수": st.column_config.NumberColumn(format="percent"),
                    "충족도": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                },
            )
            st.caption(
                "내 단계는 사이드바 '수준 진단하기'에서 고른 세부 능력 단계로 계산합니다. 기준 단계는 공고 문장을 단계 예시와 "
                "비교해 정한 값이고, 공고가 부족하면 3단계(팀 결정값)와 '불확실'로 둡니다. "
                "충족도 = 필수 비중 × (기준 이상이면 1) + 우대 비중 × 내 단계/5."
            )
        if have:
            st.caption("이미 보유: " + ", ".join(
                r["skill"] + (" (불확실)" if r["required_level_confidence"] == "uncertain" else "") for r in have))

    with st.container(border=True):
        st.subheader("현직자에게 물어보기")
        render_connect(ctx)




def render(ctx: Ctx):
    gap_err = ctx.gap_err
    if gap_err:
        if isinstance(gap_err, Pending):
            empty_metrics(["분석한 공고", "주요 요구 기술", "그중 보유", "부족"])
            notice("능력 범주별 Gap", gap_err, "영역마다 공고 수요와 내가 가진 몫이 여기에 나옵니다.")
            notice("채용 시장 Gap", gap_err, "공고가 원하는 기술과 교육이 다루는 기술의 차이가 여기에 나옵니다.")
            notice("부족한 기술", gap_err, "보유 기술과 비교해 부족한 기술이 수요 높은 순으로 여기에 나옵니다.")
        else:
            notice("부족 역량", gap_err)
    else:
        _render_gap(ctx)
