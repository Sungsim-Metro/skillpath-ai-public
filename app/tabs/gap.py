"""부족 역량 화면 (Q2 어떤 근거로 필요하다 판단했나).

먼저 채울 기술 카드, 영역별 부족 정도, 영역별 역량, 부족한 기술 표를 펼쳐 두고 교육 공급·채용 시장 비교는 아래에 접어 둔다(2026-11-20).
"""
import altair as alt
import pandas as pd
import streamlit as st

from app import ui
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
    logger,
    md_escape,
    notice,
)
from app.tabs.radar import render_radar
from backend import api
from backend.api import GAP_MIN_SHARE
from backend.industry_gap import job_gap_csv
from backend.jobs import JOBS
from pipeline.market_gap import PROGRAM_CATALOGS


def render_area_gap(ctx: Ctx):
    """능력 범주별 Gap: 영역마다 공고 수요(옅은 막대) 위에 내가 가진 몫(진한 막대)을 겹친다."""
    rows, err = load(api.get_area_gap, ctx.job, ctx.levels)
    if err:
        notice("영역별 부족 정도", err)
        return
    shown = [r for r in rows if r["demand"] > 0]
    if not shown:
        st.info("이 직무의 공고에서 수요가 확인된 능력 범주가 없습니다.")
        return
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
        "옅은 막대는 공고가 그 영역의 기술을 요구한 건수, 진한 막대는 그중 내가 채운 몫입니다."
        + (f" 이 데이터에서 수요가 없는 영역: {', '.join(empty)}." if empty else "")
    )


def render_supply() -> None:
    """교육과정 목록의 기술별 과정 수를 보여준다."""
    supply, err = load(api.get_supply)
    if err:
        notice("교육 공급", err)
        return
    st.caption(f"{md_escape(supply['source'])} · 전체 학습 프로그램 {supply['total_courses']:,}개")
    if supply.get("note"):
        st.caption(md_escape(supply["note"]))
    if not supply["skills"]:
        st.info("표시할 기술별 과정 수가 없습니다.")
        return
    table = pd.DataFrame(supply["skills"]).rename(columns={"skill": "기술", "courses": "과정 수"})
    st.dataframe(table.sort_values(["과정 수", "기술"], ascending=[False, True]), hide_index=True, width="stretch")


def render_market_gap(ctx: Ctx) -> None:
    """선택한 직군의 공고 수요와 학습 프로그램 공급 비중을 비교한다."""
    job_title = JOBS[ctx.job]
    try:
        market = api.get_market_gap(ctx.job)
    except FileNotFoundError:
        catalogs = ", ".join(f"{name} (`{path.as_posix()}`)" for name, path in PROGRAM_CATALOGS.items())
        st.info(
            f"{job_title}의 시장 Gap 자료를 찾지 못했습니다. 이 직군의 공고와 학습 프로그램 목록({catalogs})을 확인하세요."
        )
        return
    except (OSError, ValueError) as exc:
        logger.exception("시장 Gap 입력 읽기 실패")
        st.error("채용 시장 비교 자료를 읽을 수 없습니다. 잠시 뒤 다시 시도해 주세요.")
        return

    st.caption(f"{job_title} · {md_escape(market['source'])}")
    if not market["rows"]:
        st.info(f"{job_title}의 공고에서 기술 사전과 매칭된 기술이 없습니다.")
        return

    df = pd.DataFrame(market["rows"]).rename(columns={
        "job_category": "직군", "skill": "기술", "demand_pct": "수요 비중", "supply_pct": "공급 비중", "gap_pp": "Gap",
    })
    df["직군"] = df["직군"].map(JOBS)
    df["내 보유"] = df["기술"].isin(ctx.owned).map({True: "보유", False: "-"})
    st.caption(
        f"Gap = {job_title} 공고의 기술 수요 비중 − 학습 프로그램의 기술 공급 비중. "
        "양수이면 채용 수요가, 음수이면 프로그램 공급이 더 큽니다. "
        "공고마다 각 기술이 등장한 칸 중 가장 높은 가중치를 적용합니다. "
        "혜택·복지와 회사소개는 수요에서 빼고, 한 프로그램에 같은 기술이 여러 번 나와도 한 번만 셉니다."
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**기술별 수요·공급 비중 (%)**")
        selected = df.nlargest(MARKET_ROWS, "수요 비중")
        long = selected.melt(
            id_vars=["기술"], value_vars=["수요 비중", "공급 비중"], var_name="구분", value_name="비중",
        )
        bars = alt.Chart(long).mark_bar().encode(
            y=alt.Y("기술:N", sort=selected["기술"].tolist(), title=None),
            x=alt.X("비중:Q", title="비중 (%)"),
            yOffset=alt.YOffset("구분:N", sort=["수요 비중", "공급 비중"]),
            color=alt.Color("구분:N", scale=alt.Scale(domain=["수요 비중", "공급 비중"],
                                                    range=[MARKET_DEMAND, MARKET_SUPPLY]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["기술:N", "구분:N", alt.Tooltip("비중:Q", title="비중 (%)", format=".1f")],
        ).properties(height=30 * len(selected))
        st.altair_chart(bars, width="stretch")

    with right:
        st.markdown("**기술별 시장 Gap (%p)**")
        ranked = df.nlargest(MARKET_ROWS, "Gap")
        gaps = alt.Chart(ranked).mark_bar().encode(
            x=alt.X("Gap:Q", title="Gap (%p)"),
            y=alt.Y("기술:N", sort=ranked["기술"].tolist(), title=None),
            color=alt.condition(alt.datum.Gap > 0, alt.value(MARKET_DEMAND), alt.value(MARKET_SUPPLY)),
            tooltip=["기술:N", alt.Tooltip("수요 비중:Q", title="수요 (%)", format=".1f"),
                     alt.Tooltip("공급 비중:Q", title="공급 (%)", format=".1f"),
                     alt.Tooltip("Gap:Q", title="Gap (%p)", format="+.1f"), "내 보유:N"],
        ).properties(height=30 * len(ranked))
        st.altair_chart(gaps, width="stretch")

    # 이 구역이 접힌 칸 안에 있어 표를 다시 접지 않는다(접는 칸은 겹칠 수 없다)
    st.markdown(f"**{job_title} 전체 기술 표**")
    st.dataframe(df, hide_index=True, width="stretch", column_config={
        "수요 비중": st.column_config.NumberColumn("수요 비중 (%)", format="%.1f"),
        "공급 비중": st.column_config.NumberColumn("공급 비중 (%)", format="%.1f"),
        "Gap": st.column_config.NumberColumn("Gap (%p)", format="%.1f"),
    })
    st.download_button("선택 직군 CSV 내려받기", data=job_gap_csv(market["rows"]),
                       file_name=f"learning_program_gap_{ctx.job}.csv",
                       mime="text/csv", key="market_gap_csv", on_click="ignore")


PRIORITY_CARDS = 3  # '먼저 채울 기술'에 카드로 올리는 수


def _render_priorities(missing: list[dict]) -> None:
    """먼저 채울 기술: 부족한 기술의 앞쪽 몇 개를 카드로 올려 무엇이 왜 부족한지 한눈에 보인다.

    순서와 숫자는 backend.api.get_gap 이 준 그대로다(수요 높은 순). 화면은 앞에서부터 그리기만 한다.
    """
    if not missing:
        return
    st.subheader("먼저 채울 기술")
    top = missing[:PRIORITY_CARDS]
    for number, (column, r) in enumerate(zip(st.columns(len(top)), top)):
        guessed = " (불확실)" if r["required_level_confidence"] == "uncertain" else ""
        with column, st.container(border=True, key=f"priority_{number}"):
            st.markdown(f"##### {number + 1}. {md_escape(r['skill'])}")
            if r.get("area"):  # 처음 보는 기술 이름이어도 어느 분야인지 알 수 있게 영역을 함께 적는다(설문 2026-11-20)
                st.caption(md_escape(r["area"]))
            st.markdown(f"공고 {r['of']}건 중 **{r['postings']}건**이 요구 · 그중 필수 {r['required_ratio']:.0%}")
            st.markdown(f"내 단계 **{r['user_level']}** → 기준 **{r['required_level']}단계**{guessed}")
            st.button("공고 근거 보기", key=f"evidence_{number}", on_click=ui.go, args=("demand",),
                      kwargs={"evidence_skill": r["skill"]}, icon=":material/format_quote:", width="stretch",
                      help="이 기술을 요구한 공고 문장을 시장 수요 화면에서 봅니다.")


def _render_gap(ctx: Ctx):
    demand, gap_rows, missing = ctx.demand, ctx.gap_rows, ctx.missing
    have = [r for r in gap_rows if r["owned"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석한 공고", f"{demand['total_postings']}건", border=True)
    c2.metric("주요 요구 기술", f"{len(gap_rows)}개", help=f"공고의 {GAP_MIN_SHARE:.0%} 이상이 요구한 기술", border=True)
    c3.metric("그중 보유", f"{len(have)}개", help="내 단계가 기준 단계 이상인 기술", border=True)
    c4.metric("부족", f"{len(missing)}개", border=True)
    # 숫자 바로 아래에 '먼저 채울 기술'을 둔다. 안내문과 표는 그 뒤로 미루고 접어, 한 화면에서 볼 것을 줄인다(설문 2026-11-20)
    _render_priorities(missing)
    guessed = [r for r in gap_rows if r["required_level_confidence"] == "uncertain"]
    if guessed:
        st.caption(
            f":material/info: 주요 요구 기술 {len(gap_rows)}개 중 {len(guessed)}개는 공고로 기준 단계를 정하지 못해 "
            "추정값(3단계)으로 판정했습니다. '불확실'로 표시하며 참고용입니다."
        )

    with st.container(border=True):
        st.subheader("영역별 부족 정도")
        render_area_gap(ctx)

    # 영역 막대와 같은 내용을 다른 모양으로 보여 주는 차트라 접어 둔다
    with st.expander("영역별 역량: 지금과 추천 과정을 마친 뒤"):
        render_radar(ctx)

    with st.expander(f"부족한 기술 {len(missing)}개 전체 표 (수요 높은 순)"):
        if not gap_rows:
            st.info("이 직무에서 보유 수준과 비교할 주요 요구 기술이 없습니다.")
        elif not missing:
            st.success("주요 요구 기술을 모두 보유했습니다.")
        else:
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
                "내 단계는 '수준 진단 설문'의 답, 기준 단계는 공고 문장으로 정한 값입니다. "
                "충족도 = 필수 비중 × (기준 이상이면 1) + 우대 비중 × 내 단계/5."
            )
        if have:
            st.caption("이미 보유: " + ", ".join(
                r["skill"] + (" (불확실)" if r["required_level_confidence"] == "uncertain" else "") for r in have))


def render(ctx: Ctx):
    gap_err = ctx.gap_err
    if gap_err:
        if isinstance(gap_err, Pending):
            empty_metrics(["분석한 공고", "주요 요구 기술", "그중 보유", "부족"])
            notice("영역별 부족 정도", gap_err, "영역마다 공고 수요와 내가 가진 몫이 여기에 나옵니다.")
            notice("부족한 기술", gap_err, "보유 기술과 비교해 부족한 기술이 수요 높은 순으로 여기에 나옵니다.")
        else:
            notice("부족 역량", gap_err)
    else:
        _render_gap(ctx)

    with st.expander("교육 공급"):
        render_supply()

    with st.expander("채용 시장 수요와 교육 공급 차이"):
        render_market_gap(ctx)
