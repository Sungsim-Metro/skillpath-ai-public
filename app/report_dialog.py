"""사이드바 마이페이지의 진단 보고서 버튼과 창(#98). 대시보드와 학습 상담 두 화면 모두에서 받을 수 있다.

화면 코드만 둔다. 보고서 글은 app/report.py, 수강 순서는 backend/plan.py 가 만든다.
버튼을 눌러 창을 열 때만 보고서를 만든다(화면을 다시 그릴 때마다 추천을 부르지 않는다).
"""
import streamlit as st

from app.common import load
from app.report import build_report, file_name
from backend import api
from backend.jobs import JOBS
from backend.levels import level_guide
from backend.plan import study_order, uncovered_skills


def _build(job: str, levels: dict[str, int]) -> tuple[str | None, str | None]:
    """(보고서 마크다운, 안내 문구). 대시보드 탭들과 같은 함수·같은 인자로 값을 받는다."""
    demand, err = load(api.get_demand, job)
    if err:
        return None, err
    gap_rows, err = load(api.get_gap, job, levels)
    if err:
        return None, err
    areas, err = load(api.get_area_gap, job, levels)
    if err:
        return None, err
    missing = [r for r in gap_rows if not r["owned"]]
    level_of = {r["skill"]: r["user_level"] for r in missing}  # 학습 추천 탭과 같은 인자(실제 모드는 캐시를 돌려준다)
    recs, rec_err = (load(api.get_recommendations, job, [r["skill"] for r in missing], levels=level_of)
                     if missing else ([], None))
    recs = recs or []
    market, _ = load(api.get_market_gap, job)  # 못 받으면 보고서에서 그 표만 건너뛴다
    md = build_report(job_title=JOBS[job], demand=demand, gap_rows=gap_rows, area_rows=areas,
                      recommendations=recs, plan=study_order(recs), uncovered=uncovered_skills(recs),
                      market_rows=market["rows"] if market else None, level_names=level_guide()["level_names"],
                      mock=api.is_mock(), recommend_failed=bool(rec_err))
    return md, ("추천을 불러오지 못해 보고서의 추천 교육과정 절은 비어 있습니다." if rec_err else None)


@st.dialog("진단 보고서", width="large")
def report_dialog(job: str, levels: dict[str, int]) -> None:
    with st.spinner("보고서를 만드는 중입니다"):
        md, note = _build(job, levels)
    if md is None:
        st.warning(f"보고서를 만들지 못했습니다. {note}")
        return
    st.write("역량 진단, AWS 교육과정 추천, 수강 순서, 과정으로 채우기 어려운 기술을 마크다운(.md) 파일 하나로 받습니다.")
    if note:
        st.caption(note)
    # on_click="ignore": 내려받아도 화면을 다시 그리지 않아 창이 닫히지 않는다
    st.download_button("내려받기 (.md)", data=md.encode("utf-8"), file_name=file_name(), mime="text/markdown",
                       type="primary", icon=":material/download:", key="report_btn", on_click="ignore")
    st.caption("노션, GitHub, VS Code, 메모장 등에서 열 수 있습니다. 화면에 나온 값만 담기며, 이 서비스는 보고서를 저장하지 않습니다.")
    with st.container(height=480, border=True):
        st.markdown(md)  # 내려받는 파일과 같은 글. 데이터 글자는 이스케이프돼 있다(app/report.py)


def sidebar_button(job: str, levels: dict[str, int]) -> None:
    """마이페이지의 보고서 버튼. 진단 전(예시 값)에는 막는다. 예시 값 보고서가 내 보고서처럼 보이지 않게."""
    example = bool(st.session_state.get("levels_example"))
    if st.button("진단 보고서", key="open_report", icon=":material/description:", width="stretch", disabled=example,
                 help="수준 진단을 마치면 받을 수 있습니다." if example else "진단 결과를 .md 파일로 받습니다."):
        report_dialog(job, levels)
