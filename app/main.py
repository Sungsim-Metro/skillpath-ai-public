"""skillpath-ai 화면.

화면 코드만 둔다. 대시보드 집계와 상담 응답은 backend 에서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
색과 글꼴은 `.streamlit/config.toml` 에서 정한다.

이 파일은 화면 선택, 사이드바 입력과 탭 배치를 맡는다. 탭 내용은 `app/tabs/` 의 파일 하나씩에 있고,
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

from app import chat as chat_screen  # noqa: E402
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

VIEWS = {"dashboard": "역량 대시보드", "chat": "학습 상담"}


def remember_skills(key: str, displayed: list[str]) -> None:
    """사용자가 추가한 기술만 상담으로 전달한다. 이전 화면에서 고른 기술도 유지한다."""
    selected = st.session_state[key]
    removed = set(displayed) - set(selected)
    shared = [s for s in st.session_state.get("sidebar_skills", []) if s not in removed]
    shared.extend(s for s in selected if s not in displayed and s not in shared)
    st.session_state.sidebar_skills = shared
    examples = st.session_state.get("dashboard_examples", DEFAULT_OWNED)
    st.session_state.dashboard_examples = (
        [s for s in examples if s not in removed] if key == "owned" else []
    )


def skills_input(options: list[str], *, chat: bool) -> list[str]:
    key = "chat_owned" if chat else "owned"
    shared = st.session_state.get("sidebar_skills", [])
    examples = [] if chat else st.session_state.get("dashboard_examples", DEFAULT_OWNED)
    displayed = [s for s in dict.fromkeys([*shared, *examples]) if s in options]
    if st.session_state.get(key) != displayed:
        st.session_state[key] = displayed
    kwargs = {"options": options, "key": key, "on_change": remember_skills, "args": (key, displayed)}
    if chat:
        return st.multiselect("보유 기술", **kwargs, placeholder="직접 사용한 기술 선택")
    return st.pills(
        "보유 기술", **kwargs, selection_mode="multi",
        help="대시보드에는 예시 기술이 미리 선택됩니다. 직접 추가한 기술을 채팅에서도 사용합니다.",
    )


with st.sidebar:
    st.subheader("skillpath-ai")
    st.caption("채용공고 기반 역량 진단")
    if "view" not in st.session_state:
        st.session_state.view = "chat" if st.query_params.get("view") == "chat" else "dashboard"
    view = st.radio("화면", list(VIEWS), format_func=VIEWS.get, key="view")
    if view != st.query_params.get("view", "dashboard"):
        st.query_params["view"] = view
    job = st.selectbox("목표 직무", options=list(JOBS), format_func=JOBS.get, key="job")
    if view == "chat":
        nickname = chat_screen.nickname_input()
        options = list(dict.fromkeys([*chat_screen.skill_options(), *st.session_state.get("sidebar_skills", [])]))
        owned = skills_input(options, chat=True)
    else:
        demand, demand_err = load(api.get_demand, job)
        skill_options = [s["skill"] for s in demand["skills"]] if demand else []
        if skill_options:
            owned = skills_input(skill_options, chat=False)
        else:
            owned = []
            st.markdown(f"보유 기술 {PENDING}")
            st.caption("고를 수 있는 기술 목록은 채용공고 수요 집계가 연결되면 나옵니다.")
    st.divider()
    if api.is_mock():
        st.caption("가짜 데이터 모드입니다 (BEDROCK_MOCK=1). 숫자와 과정은 예시입니다.")
    else:
        st.caption("실제 모드입니다 (BEDROCK_MOCK=0).")

if view == "chat":
    chat_screen.render(job, owned, nickname)
    st.stop()

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
