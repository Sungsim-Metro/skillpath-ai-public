"""사이드바 마이페이지와 보유 수준 진단 모달 (#90).

화면 코드만 둔다. 단계 예시는 backend.levels.level_guide(), 저장은 backend.chat_profiles 가 맡는다.
세션 값:
- levels: {세부 능력 id: 0~5}. 대시보드 Gap 과 상담 채팅의 보유 판정에 쓴다
- levels_example: True 면 아직 진단 전이라 예시 값이다(상담 채팅에는 넘기지 않는다)
- levels_owner: levels 를 불러온 닉네임
"""
import logging

import streamlit as st

from backend import chat_profiles
from backend import api
from backend.levels import job_subskills, level_guide

# 진단 전에 보여줄 예시: 예전 기본 보유 기술 Python·Linux·Git 에 해당하는 세부 능력 3단계
# 클라우드(C*)는 스크립트·자동화, Linux, Git. AI(A*)는 데이터 분석(Python), 배포 자동화(Git)
EXAMPLE_LEVELS = {"C5-2": 3, "C1-2": 3, "C5-3": 3, "A2-2": 3, "A4-3": 3}
EXAMPLE_TEXT = {"cloud_engineer": "스크립트·자동화, Linux, Git 3단계", "ai_engineer": "데이터 분석·특징 추출, 배포 자동화 3단계"}
logger = logging.getLogger("skillpath.app")


def _areas(job: str) -> dict[str, str]:
    """그 직무의 영역 {id: 이름}(공통 제외). 영역 표는 backend 가 읽는다."""
    return api.get_areas(job)


def _set(levels: dict[str, int], example: bool) -> None:
    st.session_state.levels = dict(levels)
    st.session_state.levels_example = example
    for key in [k for k in st.session_state if str(k).startswith("lv_")]:  # 진단 창의 작성 중 값은 버린다
        del st.session_state[key]


def sync_with_profile(nickname: str) -> None:
    """닉네임이 바뀌면 그 닉네임에 저장한 수준을 불러온다.

    저장한 수준이 없으면: 손님 → 닉네임은 지금 고른 값을 이어 쓰고, 다른 닉네임으로 바꾸거나 지우면 예시 값으로 되돌린다.
    """
    if "levels" not in st.session_state:
        _set(EXAMPLE_LEVELS, True)
        st.session_state.levels_owner = ""
    previous = st.session_state.get("levels_owner", "")
    if nickname == previous:
        return
    st.session_state.levels_owner = nickname
    saved = {}
    if nickname:
        try:
            saved = chat_profiles.load_profile(nickname)["levels"]
        except Exception:  # noqa: BLE001 - 저장소 오류로 화면 전체가 멈추지 않게. 닉네임은 로그에 남기지 않는다
            logger.exception("프로필 수준을 불러오지 못했습니다")
            st.session_state.levels_notice = "저장한 수준을 불러오지 못했습니다."
    if saved:
        _set(saved, False)
    elif previous:
        _set(EXAMPLE_LEVELS, True)


SECONDS_PER_QUESTION = 10  # 예상 응답 시간 계산용 어림값(측정한 값이 아님)
# 단계 카드(버튼) 모양. 카드마다 key 가 lvb_ 로 시작해 st-key-lvb_… 클래스가 붙는다
CARD_CSS = """
<style>
[class*="st-key-lvb_"] button {
  min-height: 8.5rem; height: 100%; width: 100%;
  align-items: flex-start; justify-content: flex-start; text-align: left;
  padding: .6rem .7rem; border-radius: .6rem;
  transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, background-color .12s ease;
}
[class*="st-key-lvb_"] button p { white-space: normal; text-align: left; font-size: .84rem; line-height: 1.35; }
[class*="st-key-lvb_"] button:hover {
  transform: translateY(-2px); border-color: #1F5FBF; box-shadow: 0 4px 12px rgba(31, 95, 191, .18);
}
[class*="st-key-lvb_"] button:active { transform: translateY(0); box-shadow: 0 1px 4px rgba(31, 95, 191, .25); }
[class*="st-key-lvb_"] button[data-testid="stBaseButton-primary"] { box-shadow: 0 0 0 3px rgba(31, 95, 191, .25); }
/* 질문 목록만 스크롤하고 제목·진행 바·영역 제목·이동 버튼은 고정한다. 페이지마다 key 가 달라 넘기면 맨 위부터 보인다 */
[class*="st-key-lvq_box"] {
  max-height: calc(100vh - 400px); min-height: 220px; overflow-y: auto; overflow-x: hidden;
  padding: 4px .6rem 4px 1.1rem; border-left: 3px solid #D6E2F6;
}
</style>
"""


def _choose(sid: str, level: int) -> None:
    st.session_state[f"lv_{sid}"] = level


def _move(step: int) -> None:
    st.session_state.lv_page += step


@st.dialog("보유 수준 진단", width="large")
def diagnose_dialog(nickname: str, job: str) -> None:
    """영역 하나씩 페이지로 넘기며 세부 능력마다 0~5단계 카드를 눌러 고른다. 질문은 고른 직무의 진단표에서 온다."""
    names = level_guide()["level_names"]
    subskills = job_subskills(job)
    areas = list(_areas(job).items())
    # 예시 값은 사용자가 고른 것이 아니므로 빈 설문에서 시작한다
    current = {} if st.session_state.get("levels_example") else st.session_state.get("levels", {})
    for sid in subskills:  # 처음 열 때만 지금 값으로 채운다. 저장하거나 닉네임이 바뀌면 지운다
        st.session_state.setdefault(f"lv_{sid}", current.get(sid))
    st.session_state.setdefault("lv_page", 0)
    page = min(max(st.session_state.lv_page, 0), len(areas) - 1)
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    answered = sum(st.session_state[f"lv_{sid}"] is not None for sid in subskills)
    left = len(subskills) - answered
    minutes = max(1, round(left * SECONDS_PER_QUESTION / 60))
    st.progress(answered / len(subskills), text=f"{answered} / {len(subskills)}개 응답"
                + (f" · 남은 시간 약 {minutes}분" if left else " · 완료"))
    st.caption(f"영역 {len(areas)}개 · 질문 {len(subskills)}개 · 약 "
               f"{max(1, round(len(subskills) * SECONDS_PER_QUESTION / 60))}분(질문당 10초 어림). "
               "지금 할 수 있는 일에 가장 가까운 카드를 누르세요. 시험이 아니며, 예시는 팀이 확인 중인 AI 초안입니다.")

    area_id, area_name = areas[page]
    rows = [(sid, row) for sid, row in subskills.items() if row["area_id"] == area_id]
    done = sum(st.session_state[f"lv_{sid}"] is not None for sid, _ in rows)
    title, count = st.columns([3, 1], vertical_alignment="bottom")
    title.subheader(f"{page + 1}/{len(areas)} · {area_name}")
    count.markdown(f"<div style='text-align:right'>이 영역 <b>{done}/{len(rows)}</b>개 응답</div>",
                   unsafe_allow_html=True)
    with st.container(key=f"lvq_box_{page}"):
        for i, (sid, row) in enumerate(rows):
            if i:
                st.divider()
            _question(sid, row, names)

    back, status, forward = st.columns([1, 2, 1], vertical_alignment="center")
    back.button("이전 영역", key="lvnav_prev", disabled=page == 0, on_click=_move, args=(-1,), width="stretch")
    finished = sum(all(st.session_state[f"lv_{s}"] is not None for s, r in subskills.items() if r["area_id"] == a)
                   for a, _ in areas)
    status.caption(f"<div style='text-align:center'>완료한 영역 {finished}/{len(areas)}</div>", unsafe_allow_html=True)
    if page < len(areas) - 1:
        forward.button("다음 영역", key="lvnav_next", type="primary", on_click=_move, args=(1,), width="stretch")
        return
    status.caption(("모든 질문에 답하면 저장할 수 있습니다. " if left else "")
                   + ("닉네임 프로필에도 저장합니다." if nickname else "닉네임을 넣으면 다음에도 불러옵니다."))
    label = f"저장 (남은 질문 {left}개)" if left else "저장"
    if forward.button(label, type="primary", width="stretch", disabled=bool(left), key="save_levels"):
        # 다른 직무에서 진단한 값은 그대로 두고 이 직무의 답만 덮어쓴다
        kept = {} if st.session_state.get("levels_example") else dict(st.session_state.get("levels", {}))
        save_levels(nickname, {**kept, **{sid: st.session_state[f"lv_{sid}"] for sid in subskills}})
        st.rerun()


def _question(sid: str, row: dict, names: dict) -> None:
    """세부 능력 하나: 0~5단계 예시 카드를 가로로 놓는다. 카드를 누르면 그 단계를 고르고, 고른 카드는 강조한다."""
    chosen = st.session_state.get(f"lv_{sid}")
    st.markdown(f"**{row['name']}**" + ("" if chosen is None else f" · :blue[{chosen}단계 선택]"))
    for level, col in enumerate(st.columns(6, gap="small")):
        text = names["0"] if level == 0 else row["levels"][str(level)]
        title = "0 · 없음" if level == 0 else f"{level}단계"
        col.button(f"**{title}**  \n{text}", key=f"lvb_{sid}_{level}", on_click=_choose, args=(sid, level),
                   type="primary" if level == chosen else "secondary", width="stretch")


def save_levels(nickname: str, chosen: dict[str, int]) -> None:
    """진단 결과를 세션에 두고, 닉네임이 있으면 프로필에도 저장한다."""
    # 0(해 본 적 없음)도 답한 값이라 남긴다. 계산에서는 0과 진단하지 않은 것이 같다
    _set({sid: v for sid, v in chosen.items() if v is not None}, False)
    if not nickname:
        return
    try:
        # 0으로 바꾼 세부 능력도 덮어쓰게 고른 값 전체를 넘긴다
        chat_profiles.update_profile(nickname, levels=dict(chosen))
    except Exception:  # noqa: BLE001
        logger.exception("프로필 수준을 저장하지 못했습니다")
        st.session_state.levels_notice = "수준을 프로필에 저장하지 못했습니다. 이번 화면에서만 사용합니다."


def sidebar_summary(nickname: str, job: str) -> dict[str, int]:
    """사이드바에 고른 직무의 영역별 내 수준을 보여주고 진단 버튼을 둔다. 지금 쓰는 {세부 능력 id: 단계} 를 돌려준다."""
    sync_with_profile(nickname)
    levels = st.session_state.levels
    guide = job_subskills(job)
    st.markdown("**내 보유 수준**")
    if st.session_state.get("levels_example"):
        st.caption(f"아직 진단 전이라 예시 값({EXAMPLE_TEXT.get(job, '예시')})으로 보여줍니다.")
    elif not any(levels.get(sid) is not None for sid in guide):
        st.caption("이 직무는 아직 진단하지 않았습니다. 진단하지 않은 세부 능력은 0단계로 봅니다.")
    lines = []
    for area_id, area_name in _areas(job).items():
        values = [levels.get(sid, 0) for sid, row in guide.items() if row["area_id"] == area_id]
        avg = sum(values) / len(values) if values else 0
        lines.append(f"{area_name} {avg:.1f}")
    st.caption(" · ".join(lines) + " (영역 평균, 0~5)")
    if st.button("수준 진단하기", key="open_levels", icon=":material/checklist:", width="stretch"):
        diagnose_dialog(nickname, job)
    notice = st.session_state.pop("levels_notice", None)
    if notice:
        st.warning(notice)
    return dict(levels)


def chat_skills(job: str, levels: dict[str, int]) -> list[str]:
    """상담 채팅에 넘길 보유 기술 이름. 예시 값은 넘기지 않는다(사용자가 고른 값만 보유로 본다)."""
    if st.session_state.get("levels_example") or not levels:
        return []
    from app.common import load
    from backend import api

    names, _ = load(api.owned_skill_names, job, levels)
    return names or []
