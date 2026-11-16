"""skillpath-ai 화면.

화면 코드만 둔다. 데이터와 계산은 backend.api 의 함수를 호출해서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
색과 글꼴은 `.streamlit/config.toml` 에서 정한다.

이 파일은 사이드바 입력과 탭 배치만 맡는다. 탭 내용은 `app/tabs/` 의 파일 하나씩에 있고,
함께 쓰는 상수와 도우미는 `app/common.py` 에 있다. 새 탭은 `app/tabs/` 에 파일을 더하고 아래 TABS 에 한 줄을 넣는다.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend import api  # noqa: E402
from backend.jobs import JOBS  # noqa: E402

from app.common import DEFAULT_OWNED, PENDING, Ctx, Pending, load  # noqa: E402
from app.tabs import demand as tab_demand  # noqa: E402
from app.tabs import extract as tab_extract  # noqa: E402
from app.tabs import gap as tab_gap  # noqa: E402
from app.tabs import recommend as tab_recommend  # noqa: E402

# (탭 이름, 그리는 함수). 순서대로 화면에 놓인다.
TABS = [
    ("시장 수요", tab_demand.render),
    ("부족 역량", tab_gap.render),
    ("학습 추천", tab_recommend.render),
    ("공고 직접 분석", tab_extract.render),
]

st.set_page_config(page_title="skillpath-ai", layout="wide")

# ── 사이드바: 입력 ─────────────────────────────────────────────
with st.sidebar:
    st.subheader("skillpath-ai")
    st.caption("채용공고 기반 역량 진단")
    job = st.selectbox("목표 직무", options=list(JOBS), format_func=JOBS.get, key="job")
    demand, demand_err = load(api.get_demand, job)
    skill_options = [s["skill"] for s in demand["skills"]] if demand else []
    if skill_options:
        owned = st.pills(
            "보유 기술",
            options=skill_options,
            selection_mode="multi",
            default=[s for s in DEFAULT_OWNED if s in skill_options],
            key="owned",
            help="이미 다룰 수 있는 기술을 누르세요. 부족 역량과 추천이 바로 바뀝니다.",
        )
    else:
        owned = []
        st.markdown(f"보유 기술 {PENDING}")
        st.caption("고를 수 있는 기술 목록은 채용공고 수요 집계가 연결되면 나옵니다.")
    st.divider()
    if api.is_mock():
        st.caption("가짜 데이터 모드입니다 (BEDROCK_MOCK=1). 숫자와 과정은 예시입니다.")
    else:
        st.caption("실제 모드입니다 (BEDROCK_MOCK=0).")

if demand:
    gap_rows, gap_err = load(api.get_gap, job, owned)
else:  # 수요 집계가 구현 전이면 이어지는 구역도 '개발 중', 오류면 오류로 보여준다
    needs_demand = "시장 수요 집계가 있어야 계산할 수 있습니다."
    gap_rows, gap_err = None, Pending(needs_demand) if isinstance(demand_err, Pending) else needs_demand
missing = [r for r in gap_rows if not r["owned"]] if gap_rows is not None else []

st.title(f"{JOBS[job]} 역량 진단")
if demand:
    origin = [f"수집일 {demand['collected']}"] if demand.get("collected") else []
    origin += [f"출처 {', '.join(demand['sources'])}"] if demand.get("sources") else []
    st.caption(
        f"채용공고 {demand['total_postings']}건을 분석해 부족한 역량과 AWS 교육과정을 근거와 함께 보여줍니다."
        + (f" {', '.join(origin)}." if origin else "")
    )
else:
    st.caption("채용공고를 분석해 부족한 역량과 AWS 교육과정을 근거와 함께 보여줍니다.")

ctx = Ctx(job=job, demand=demand, demand_err=demand_err, owned=list(owned), skill_options=skill_options,
          gap_rows=gap_rows, gap_err=gap_err, missing=missing)
for container, (_, render) in zip(st.tabs([name for name, _ in TABS]), TABS):
    with container:
        render(ctx)
