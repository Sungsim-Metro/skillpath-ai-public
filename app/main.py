"""skillpath-ai 화면.

화면 코드만 둔다. 데이터와 계산은 backend.api 의 함수를 호출해서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
"""
import logging
import re
import sys
from pathlib import Path

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
    st.header("내 정보")
    job = st.selectbox("목표 직무", options=list(JOBS), format_func=JOBS.get, key="job")
    demand, demand_err = load(api.get_demand, job)
    skill_options = [s["skill"] for s in demand["skills"]] if demand else []
    owned = st.multiselect(
        "보유 기술",
        options=skill_options,
        default=[s for s in DEFAULT_OWNED if s in skill_options],
        key="owned",
        help="이미 다룰 수 있는 기술을 고르세요. 부족 역량과 추천이 바로 바뀝니다.",
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

st.title("skillpath-ai")
if demand:
    st.caption(
        f"{JOBS[job]} 채용공고 {demand['total_postings']}건을 분석해 부족한 역량과 AWS 교육과정을 근거와 함께 보여줍니다."
    )


# ── 탭 1: 시장 수요 (Q1 어떤 데이터를 분석했나) ──────────────────
def render_demand():
    total = demand["total_postings"]
    c1, c2, c3 = st.columns(3)
    c1.metric("분석한 공고", f"{total}건")
    c2.metric("추출한 기술", f"{len(demand['skills'])}개")
    c3.metric("수집일", demand["collected"])
    st.caption(f"출처: {', '.join(demand['sources'])}" + (f" · {demand['note']}" if demand.get("note") else ""))

    df = pd.DataFrame(
        [{"기술": s["skill"], "필수": s["required"], "우대": s["preferred"]} for s in demand["skills"]]
    )
    st.subheader("이 직무가 요구하는 기술")
    st.bar_chart(df, x="기술", y=["필수", "우대"], horizontal=True, sort=False,
                 color=["#1f5fbf", "#9bbbe8"], height=380, x_label="", y_label="요구한 공고 수 (건)")

    st.subheader("근거 보기")
    pick = st.selectbox("기술을 고르면 공고 원문 근거를 보여줍니다", skill_options, key="evidence_skill")
    s = next(x for x in demand["skills"] if x["skill"] == pick)
    st.write(f"**{pick}**: 공고 {total}건 중 {s['postings']}건 (필수 {s['required']}건, 우대 {s['preferred']}건)")
    for ev in s["evidence_samples"]:
        st.markdown(f"> {md_escape(ev['evidence'])}  \n> `{ev['posting_id']}`")

# ── 탭 2: 부족 역량 (Q2 어떤 근거로 필요하다 판단했나) ─────────────
def render_gap():
    have = [r for r in gap_rows if r["owned"]]
    c1, c2 = st.columns(2)
    c1.metric("주요 요구 기술", f"{len(gap_rows)}개", help="공고의 20% 이상이 요구한 기술")
    c2.metric("그중 보유", f"{len(have)}개")

    if not missing:
        st.success("주요 요구 기술을 모두 보유했습니다.")
    else:
        st.subheader(f"부족한 기술 {len(missing)}개 (수요 높은 순)")
        st.dataframe(
            pd.DataFrame([
                {
                    "기술": r["skill"],
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
            for c in rec["courses"]:
                badge = f" {UNCERTAIN}" if c["match"] == "uncertain" else ""
                st.markdown(f"**[{md_escape(c['title'])}]({c['url']})**{badge}")
                st.caption(f"난이도 {c['level']} · 선수지식 {c['prerequisites']}")
                st.write(c["reason"])

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
            st.subheader("근거 표시")
            st.markdown(highlighted.replace("\n", "  \n"))
            st.subheader("추출 결과")
            st.dataframe(
                pd.DataFrame([
                    {
                        "기술": sk["normalized"],
                        "공고 표현": sk["raw"],
                        "구분": "필수" if sk["type"] == "required" else "우대",
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
