"""skillpath-ai 화면.

화면 코드만 둔다. 대시보드 집계와 상담 응답은 backend 에서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
색과 글꼴은 `.streamlit/config.toml` 에서 정한다.

이 파일은 화면 선택, 사이드바 입력과 탭 배치를 맡는다. 탭 내용은 `app/tabs/` 의 파일 하나씩에 있고,
함께 쓰는 상수와 도우미는 `app/common.py` 에 있다. 새 탭은 `app/tabs/` 에 파일을 더하고 아래 TABS 에 한 줄을 넣는다.
"""
import os
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

# Streamlit은 app/ 밖의 모듈을 다시 불러올 때 PYTHONPATH를 확인한다.
pythonpath = os.environ.get("PYTHONPATH", "")
if str(ROOT) not in pythonpath.split(os.pathsep):
    os.environ["PYTHONPATH"] = str(ROOT) + (os.pathsep + pythonpath if pythonpath else "")

from backend import api  # noqa: E402
from backend.jobs import JOBS  # noqa: E402

from app import chat as chat_screen  # noqa: E402
from app import levels as levels_screen  # noqa: E402
from app import report_dialog  # noqa: E402
from app.common import Ctx, Pending, load  # noqa: E402
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

# 관리자 화면은 주소 /admin?admin=<ADMIN_KEY 값> 으로만 연다(2026-11-19 회의). 값이 맞을 때만 페이지를 등록하므로
# 값이 없거나 틀리면 /admin 주소 자체가 없다. 로그인이 아니라 가려 두는 것이다. 메뉴는 숨겨서 사용자 화면은 전과 같다.
from app import admin as admin_screen  # noqa: E402

_pages = [st.Page(lambda: None, title="skillpath-ai", default=True)]
if admin_screen.allowed(st.query_params.get("admin", "")):
    _pages.append(st.Page(admin_screen.render, title="관리자", url_path="admin"))
if st.navigation(_pages, position="hidden").url_path == "admin":
    admin_screen.render()
    st.stop()

VIEWS = {"dashboard": "역량 대시보드", "chat": "학습 상담"}


with st.sidebar:
    st.subheader("skillpath-ai")
    st.caption("채용공고 기반 역량 진단")
    if "view" not in st.session_state:
        st.session_state.view = "chat" if st.query_params.get("view") == "chat" else "dashboard"
    view = st.radio("화면", list(VIEWS), format_func=VIEWS.get, key="view")
    if view != st.query_params.get("view", "dashboard"):
        st.query_params["view"] = view
    session = chat_screen.profile_access()
    job = st.selectbox("목표 직무", options=list(JOBS), format_func=JOBS.get, key="job")
    levels = levels_screen.sidebar_summary(session, job)
    report_dialog.sidebar_button(job, levels)
    demand, demand_err = (None, None) if view == "chat" else load(api.get_demand, job)
    skill_options = [s["skill"] for s in demand["skills"]] if demand else []
    st.divider()
    if api.is_mock():
        st.caption("가짜 데이터 모드입니다 (BEDROCK_MOCK=1). 개인 역량 진단과 추천 과정은 예시입니다.")
    else:
        st.caption("실제 모드입니다 (BEDROCK_MOCK=0).")

if view == "chat":
    # 상담 채팅은 기술 이름 목록을 받는다. 진단한 단계로 기준을 넘은 기술만 보유로 넘긴다
    chat_screen.render(job, levels_screen.chat_skills(job, levels), session)
    st.stop()

if demand:
    gap_rows, gap_err = load(api.get_gap, job, levels)
else:  # 수요 집계가 구현 전이면 이어지는 구역도 '개발 중', 오류면 오류로 보여준다
    needs_demand = "시장 수요 집계가 있어야 계산할 수 있습니다."
    gap_rows, gap_err = None, Pending(needs_demand) if isinstance(demand_err, Pending) else needs_demand
missing = [r for r in gap_rows if not r["owned"]] if gap_rows is not None else []

st.title(f"{JOBS[job]} 역량 진단")
if demand:
    origin = [f"수집일 {demand['collected']}"] if demand.get("collected") else []
    origin += [f"출처 {', '.join(demand['sources'])}"] if demand.get("sources") else []
    st.caption(
        f"채용공고 {demand['total_postings']}건을 분석해 부족한 역량과 학습 프로그램을 근거와 함께 보여줍니다."
        + (f" {', '.join(origin)}." if origin else "")
    )
else:
    st.caption("채용공고를 분석해 부족한 역량과 학습 프로그램을 근거와 함께 보여줍니다.")

owned, _ = load(api.owned_skill_names, job, levels) if demand else ([], None)
ctx = Ctx(job=job, demand=demand, demand_err=demand_err, owned=owned or [], levels=levels, skill_options=skill_options,
          gap_rows=gap_rows, gap_err=gap_err, missing=missing)
for container, (_, render) in zip(st.tabs([name for name, _ in TABS]), TABS):
    with container:
        render(ctx)
