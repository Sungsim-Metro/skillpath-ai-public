"""skillpath-ai 화면.

화면 코드만 둔다. 데이터와 계산은 backend.api 의 함수를 호출해서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
색과 글꼴은 `.streamlit/config.toml` 에서 정한다.
"""
import logging
import re
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend import api  # noqa: E402

JOBS = {"cloud_engineer": "클라우드 엔지니어"}
SAMPLE_POSTING = (
    "클라우드 엔지니어 채용\n"
    "Python을 활용한 개발 경험과 AWS 환경 운영 경험 필수, Docker 사용 경험 우대.\n"
    "Linux 서버 운영 및 트러블슈팅 경험. 쿠버네티스 운영 경험자 우대."
)
UNCERTAIN = ":orange-badge[불확실]"
DEFAULT_OWNED = ["Python", "Linux", "Git"]
TYPE_LABEL = {"required": "필수", "duty": "주요 업무", "preferred": "우대"}

# 차트 색. 파랑은 "내 것", 주황은 "시장이 원하는 것"으로만 쓴다.
ACCENT = "#1F5FBF"
ACCENT_LIGHT = "#D6E2F6"
ACCENT_LINE = "#9DB4DA"
MARKET_DEMAND = "#C2570C"
MARKET_SUPPLY = "#7C8796"
MARKET_ROWS = 16  # 시장 Gap 차트에 보여줄 기술 수. 넘으면 Gap 이 큰 쪽과 작은 쪽만 남긴다

logger = logging.getLogger("skillpath.app")


def md_escape(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#+\-!|~:$])", r"\\\1", text)


def load(fn, *args):
    """backend 함수를 부르고 (결과, 안내 문구)를 돌려준다.

    M2 통합 때 함수가 하나씩 실제 구현으로 바뀌므로, 한 함수가 아직 없거나 실패해도
    화면 전체가 멈추지 않고 그 구역에만 안내를 띄운다.
    오류 내용에는 계정 ID 같은 값이 들어 있을 수 있어 화면에는 오류 종류만 보이고 전체는 터미널 로그에 남긴다.
    """
    try:
        return fn(*args), None
    except NotImplementedError as e:
        return None, f"아직 실제 구현 전입니다. {e}"
    except Exception as e:  # noqa: BLE001 - 시연 중 한 구역 오류로 화면 전체가 멈추지 않게
        logger.exception("%s 호출 실패", fn.__name__)
        return None, f"불러오지 못했습니다({type(e).__name__}). 실행한 터미널의 로그를 확인하세요."


st.set_page_config(page_title="skillpath-ai", layout="wide")

# ── 사이드바: 입력 ─────────────────────────────────────────────
with st.sidebar:
    st.subheader("skillpath-ai")
    st.caption("채용공고 기반 역량 진단")
    job = st.selectbox("목표 직무", options=list(JOBS), format_func=JOBS.get, key="job")
    demand, demand_err = load(api.get_demand, job)
    skill_options = [s["skill"] for s in demand["skills"]] if demand else []
    owned = st.pills(
        "보유 기술",
        options=skill_options,
        selection_mode="multi",
        default=[s for s in DEFAULT_OWNED if s in skill_options],
        key="owned",
        help="이미 다룰 수 있는 기술을 누르세요. 부족 역량과 추천이 바로 바뀝니다.",
    )
    st.divider()
    if api.is_mock():
        st.caption("가짜 데이터 모드입니다 (BEDROCK_MOCK=1). 숫자와 과정은 예시입니다.")
    else:
        st.caption("실제 모드입니다 (BEDROCK_MOCK=0).")

if demand:
    gap_rows, gap_err = load(api.get_gap, job, owned)
else:
    gap_rows, gap_err = None, "시장 수요 집계가 있어야 계산할 수 있습니다."
missing = [r for r in gap_rows if not r["owned"]] if gap_rows is not None else []

st.title(f"{JOBS[job]} 역량 진단")
if demand:
    st.caption(
        f"채용공고 {demand['total_postings']}건을 분석해 부족한 역량과 AWS 교육과정을 근거와 함께 보여줍니다. "
        f"수집일 {demand['collected']}, 출처 {', '.join(demand['sources'])}."
    )


# ── 탭 1: 시장 수요 (Q1 어떤 데이터를 분석했나) ──────────────────
def render_demand():
    total = demand["total_postings"]
    c1, c2, c3 = st.columns(3)
    c1.metric("분석한 공고", f"{total}건", border=True)
    c2.metric("추출한 기술", f"{len(demand['skills'])}개", border=True)
    c3.metric("수집일", demand["collected"], border=True)
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


# ── 탭 2: 부족 역량 (Q2 어떤 근거로 필요하다 판단했나) ─────────────
def render_area_gap():
    """능력 범주별 Gap: 영역마다 공고 수요(옅은 막대) 위에 내가 가진 몫(진한 막대)을 겹친다."""
    rows, err = load(api.get_area_gap, job, owned)
    if err:
        st.info(f"능력 범주별 Gap: {err}")
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
        "옅은 막대는 공고가 그 영역의 기술을 요구한 건수, 진한 막대는 그중 내가 가진 기술의 몫입니다. "
        "비어 있는 만큼이 Gap입니다. 여러 영역에 걸치는 기술(AWS 등)은 공통, 사전에 없는 기술은 미분류로 셉니다."
        + (f" 이 데이터에서 수요가 없는 영역: {', '.join(empty)}." if empty else "")
    )


def render_market_gap():
    """채용 시장 Gap: 차트 ① 수요 vs 공급 비중, 차트 ② 기술별 Gap(%p). 계산은 backend 가 한다."""
    market, err = load(api.get_market_gap, job)
    if err:
        st.info(f"채용 시장 Gap: {err}")
        return
    rows = market["rows"]
    if len(rows) > MARKET_ROWS:  # Gap 이 큰 쪽과 작은 쪽을 함께 보여준다
        rows = rows[: MARKET_ROWS - 4] + rows[-4:]
    df = pd.DataFrame([
        {"기술": r["skill"], "수요(공고)": r["demand_pct"], "공급(교과목)": r["supply_pct"], "Gap": r["gap_pp"],
         "표시": f"{r['gap_pp']:+.1f}", "내 보유": "보유" if r["skill"] in owned else "-"}
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
    w, sup = market["weights"], market["supply"]
    st.caption(
        "+면 시장은 원하는데 교육이 덜 다루는 기술, −면 교육이 상대적으로 많이 다루는 기술입니다. "
        f"수요 가중치는 필수 {w['required']}, 주요 업무 {w['duty']}, 우대 {w['preferred']}이고, "
        f"공급은 교과목 {sup['total_courses']}개(출처 {sup['source']}) 기준입니다."
        + (f" {sup['note']}" if sup.get("note") else "")
    )
    with st.expander("전체 표 보기"):
        st.dataframe(
            pd.DataFrame([
                {"기술": r["skill"], "수요 비중(%)": r["demand_pct"], "공급 비중(%)": r["supply_pct"],
                 "Gap(%p)": r["gap_pp"], "내 보유": "보유" if r["skill"] in owned else "-"}
                for r in market["rows"]
            ]),
            hide_index=True,
            width="stretch",
        )


def render_gap():
    have = [r for r in gap_rows if r["owned"]]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석한 공고", f"{demand['total_postings']}건", border=True)
    c2.metric("주요 요구 기술", f"{len(gap_rows)}개", help="공고의 20% 이상이 요구한 기술", border=True)
    c3.metric("그중 보유", f"{len(have)}개", border=True)
    c4.metric("부족", f"{len(missing)}개", border=True)

    with st.container(border=True):
        st.subheader("능력 범주별 Gap")
        render_area_gap()

    with st.container(border=True):
        st.subheader("채용 시장 Gap")
        render_market_gap()

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
                    }
                    for r in missing
                ]),
                hide_index=True,
                width="stretch",
                column_config={
                    "요구 비율": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                    "그중 필수": st.column_config.NumberColumn(format="percent"),
                },
            )
        if have:
            st.caption("이미 보유: " + ", ".join(r["skill"] for r in have))


# ── 탭 3: 학습 추천 (Q3 왜 이 교육과정을 추천했나) ────────────────
def render_recommendations():
    if not missing:
        st.info("부족한 기술이 없어 추천할 과정이 없습니다.")
        return
    recs, err = load(api.get_recommendations, job, [r["skill"] for r in missing])
    if err:
        st.info(f"학습 추천: {err}")
        return
    for rec in recs:
        d = rec["demand"]
        with st.container(border=True):
            st.subheader(rec["gap_skill"])
            st.caption(f"공고 {d['of']}건 중 {d['postings']}건이 요구 · 그중 필수 {d['required_ratio']:.0%}")
            if not rec["courses"]:
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


# ── 탭 4: 공고 직접 분석 (라이브 시연용) ──────────────────────────
def render_extract():
    st.write("채용공고 본문을 붙여 넣으면 요구 기술과 근거 문장을 뽑습니다.")
    text = st.text_area("공고 본문", value=SAMPLE_POSTING, height=160, key="posting_text")
    if st.button("분석하기", type="primary", key="extract_btn") and text.strip():
        result, err = load(api.extract_posting, text)
        if err:
            st.info(f"공고 분석: {err}")
        st.session_state["extraction"] = result
        st.session_state["extracted_text"] = text
    result = st.session_state.get("extraction")
    if result and st.session_state.get("extracted_text") == text:
        skills = result["skills"]
        if not skills:
            st.warning("알아볼 수 있는 기술을 찾지 못했습니다.")
        else:
            highlighted = md_escape(text)
            for sk in skills:
                ev = md_escape(sk["evidence"])
                highlighted = highlighted.replace(ev, f":blue-background[{ev}]", 1)
            with st.container(border=True):
                st.subheader("근거 표시")
                st.markdown(highlighted.replace("\n", "  \n"))
            st.subheader("추출 결과")
            st.dataframe(
                pd.DataFrame([
                    {
                        "기술": sk["normalized"],
                        "공고 표현": sk["raw"],
                        "구분": TYPE_LABEL.get(sk["type"], sk["type"]),
                        "근거 문장": sk["evidence"],
                        "확신": "높음" if sk["confidence"] == "high" else "불확실",
                    }
                    for sk in skills
                ]),
                hide_index=True,
                width="stretch",
            )


tab_demand, tab_gap, tab_rec, tab_extract = st.tabs(
    ["시장 수요", "부족 역량", "학습 추천", "공고 직접 분석"]
)
with tab_demand:
    if demand_err:
        st.info(f"시장 수요: {demand_err}")
    else:
        render_demand()
with tab_gap:
    if gap_err:
        st.info(f"부족 역량: {gap_err}")
    else:
        render_gap()
with tab_rec:
    if gap_err:
        st.info(f"학습 추천: {gap_err}")
    else:
        render_recommendations()
with tab_extract:
    render_extract()
