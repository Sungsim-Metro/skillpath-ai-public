"""skillpath-ai 화면.

화면 코드만 둔다. 대시보드 집계와 상담 응답은 backend 에서 받는다.
실행: 저장소 루트에서 `streamlit run app/main.py`
색과 글꼴은 `.streamlit/config.toml` 에서 정한다.

이 파일은 사이드바 입력과 화면 선택을 맡는다. 처음 접속하면 로그인·회원가입 화면을 보여 주고, 들어오면 첫 화면은
학습 상담이다. 나머지는 사이드바 메뉴에서 연다(2026-11-20).
화면 내용은 `app/tabs/` 의 파일 하나씩에 있고, 함께 쓰는 상수와 도우미는 `app/common.py` 에 있다.
새 화면은 `app/tabs/` 에 `render(ctx)` 가 있는 파일을 더하고 아래 VIEWS 에 한 줄을 넣는다.
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

from backend import api, experiments  # noqa: E402
from backend.jobs import JOBS  # noqa: E402

from app import chat as chat_screen  # noqa: E402
from app import course_card  # noqa: E402
from app import levels as levels_screen  # noqa: E402
from app import report_dialog  # noqa: E402
from app import tour  # noqa: E402
from app.common import build_ctx  # noqa: E402
from app.tabs import connect as tab_connect  # noqa: E402
from app.tabs import demand as tab_demand  # noqa: E402
from app.tabs import gap as tab_gap  # noqa: E402
from app.tabs import recommend as tab_recommend  # noqa: E402
from app.tabs import saved as tab_saved  # noqa: E402

DEFAULT_VIEW = "chat"
# view 값: (메뉴 이름, 그리는 함수, 미리 불러올 데이터). 순서대로 사이드바 메뉴에 놓인다.
# 데이터는 None(불러오지 않음), "demand"(수요), "gap"(수요와 Gap). 학습 상담은 아래에서 따로 그린다.
# "공고 직접 분석"(app/tabs/extract.py)은 메뉴에서 뺐다(2026-11-20).
VIEWS = {
    "chat": ("학습 상담", None, None),
    "demand": ("시장 수요", tab_demand.render, "demand"),
    "gap": ("부족 역량", tab_gap.render, "gap"),
    "recommend": ("학습 추천", tab_recommend.render, "gap"),
    "saved": ("담은 교육과정", tab_saved.render, None),
    "connect": ("현직자에게 물어보기", tab_connect.render, "gap"),
    "report": ("진단 보고서", report_dialog.render, None),
}
DEFAULT_JOB = next(iter(JOBS))

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

chat_screen.entry_gate()  # 로그인하거나 비회원으로 들어오기 전에는 로그인·회원가입 화면만 그린다

with st.sidebar:
    st.subheader("skillpath-ai")
    st.caption("채용공고 기반 역량 진단")
    if st.session_state.get("view") not in VIEWS:  # 첫 실행이거나 모르는 값이면 주소의 값을 보고, 없으면 학습 상담으로 연다
        asked = st.query_params.get("view")
        st.session_state.view = asked if asked in VIEWS else DEFAULT_VIEW
    st.markdown("**마이페이지**")  # 프로필(로그인), 목표 직무. 바로 아래에 화면 메뉴를 둔다
    session = chat_screen.profile_access()
    course_card.sync_saved(session)  # 로그인하면 프로필에 담아 둔 교육과정을 불러온다
    # 목표 직무는 고르는 칸을 두지 않는다. 학습 상담의 첫 화면에서 고르거나 대화로 바꾼다(2026-11-20)
    if st.session_state.get("job") not in JOBS:
        st.session_state.job = DEFAULT_JOB
    job = st.session_state.job
    st.caption(f":material/flag: 목표 직무 · **{JOBS[job]}** (학습 상담에서 바꿉니다)")
    view = st.radio("화면", list(VIEWS), format_func=lambda key: VIEWS[key][0], key="view")
    if view != st.query_params.get("view", DEFAULT_VIEW):
        st.query_params["view"] = view
    st.divider()
    levels = levels_screen.sidebar_summary(session, job)
    st.divider()
    tour.sidebar_link()
    survey = os.getenv("SURVEY_URL", "").strip()
    if survey.startswith("https://"):  # 사용자 테스트 설문 주소가 있을 때만 보인다
        st.link_button("사용 후기 설문 참여하기", survey, icon=":material/rate_review:", type="primary", width="stretch")
    if api.is_mock():
        st.caption("가짜 데이터 모드입니다 (BEDROCK_MOCK=1). 개인 역량 진단과 추천 과정은 예시입니다.")
    else:
        st.caption("실제 모드입니다 (BEDROCK_MOCK=0).")

tour.show_if_open()  # 처음 들어온 사용자에게 기능을 단계별로 보여 준다
label, render, need = VIEWS[view]
if view == "chat":
    confirmed_levels = None if st.session_state.levels_example else levels
    architecture = "supervised" if "--supervised" in sys.argv[1:] else experiments.chat_architecture()
    chat_screen.render(job, levels_screen.chat_skills(job, levels), session, confirmed_levels,
                       architecture=architecture)
    st.stop()

ctx = build_ctx(job, levels, need)  # 화면에 필요한 데이터만 불러온다(상담은 수요를 부르지 않는다)
if st.session_state.get("from_chat") != view:  # 메뉴로 다른 화면에 갔으면 표시를 지운다
    st.session_state.pop("from_chat", None)
else:  # 상담 답 아래의 버튼으로 넘어온 화면에만 돌아가는 길을 둔다
    st.button("상담으로 돌아가기", key="back_to_chat", on_click=chat_screen.back_to_chat, icon=":material/arrow_back:")
st.title(label)
if need:
    demand = ctx.demand
    origin = [f"수집일 {demand['collected']}"] if demand and demand.get("collected") else []
    origin += [f"출처 {', '.join(demand['sources'])}"] if demand and demand.get("sources") else []
    count = f" {demand['total_postings']}건" if demand else ""
    st.caption(
        f"{JOBS[job]} · 채용공고{count}을 분석해 부족한 역량과 학습 프로그램을 근거와 함께 보여줍니다."
        + (f" {', '.join(origin)}." if origin else "")
    )
else:
    st.caption(f"{JOBS[job]} 기준")
render(ctx)
