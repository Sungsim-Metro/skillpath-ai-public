"""사이드바 마이페이지의 보유 수준 요약과 보유 수준 진단 설문 (#90).

설문은 학습 상담 화면에서 연다(2026-11-20). 영역마다 단계 표시로 어디까지 했는지 보여 주고, 일부만 답해도 저장한다.

화면 코드만 둔다. 단계 예시는 backend.levels.level_guide(), 저장은 backend.chat_profiles 가 맡는다.
세션 값:
- levels: {세부 능력 id 또는 custom:기술명: 0~5}. 대시보드 Gap 과 상담 채팅의 보유 판정에 쓴다
- levels_example: True 면 아직 진단 전이라 예시 값이다(상담 채팅에는 넘기지 않는다)
- levels_owner: levels 를 불러온 닉네임
"""
from __future__ import annotations

import logging

import streamlit as st

from backend import api, chat_profiles
from backend.jobs import JOBS
from backend.levels import (CUSTOM_PREFIX, LEVELS, MAX_CUSTOM_NAME_LENGTH, chat_owned_skill_names, job_guide,
                            job_subskills, level_guide, merge_levels, named_levels)

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


def _clear_survey() -> None:
    for key in [k for k in st.session_state if str(k).startswith("lv_")]:
        del st.session_state[key]


def sync_with_profile(session: chat_profiles.ProfileSession | None) -> None:
    """계정 전환 시 저장 수준을 복원하고, 빈 계정에서만 손님의 확정 응답을 임시로 이어 쓴다."""
    owner = session.nickname if session else None
    if "levels" in st.session_state and st.session_state.get("levels_owner") == owner:
        return
    guest = {}
    if session and st.session_state.get("levels_owner") is None and not st.session_state.get("levels_example"):
        guest = dict(st.session_state.get("levels", {}))
    st.session_state.levels_owner = owner
    _clear_survey()
    for key in ("custom_level_name", "custom_level_value", "levels_notice", "levels_pending", "guest_levels"):
        st.session_state.pop(key, None)
    saved = {}
    if session:
        try:
            saved = chat_profiles.load_profile(session)["levels"]
        except Exception:  # noqa: BLE001 - 화면과 로그에 계정 정보를 표시하지 않는다
            logger.warning("프로필 수준을 불러오지 못했습니다")
            st.session_state.levels_notice = "저장한 수준을 불러오지 못했습니다."
        else:
            if not saved and guest:
                st.session_state.guest_levels = guest
                _set(guest, False)
                return
    _set(saved or EXAMPLE_LEVELS, not saved)


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
def diagnose_dialog(session: chat_profiles.ProfileSession | None, job: str) -> None:
    """영역 하나씩 페이지로 넘기며 세부 능력마다 0~5단계 카드를 눌러 고른다. 질문은 고른 직무의 진단표에서 온다."""
    names = level_guide()["level_names"]
    subskills = job_subskills(job)
    areas = list(_areas(job).items())
    # 예시 값은 사용자가 고른 것이 아니므로 빈 설문에서 시작한다
    current = {} if st.session_state.get("levels_example") else st.session_state.get("levels", {})
    baseline = st.session_state.setdefault(f"lv_initial_{job}", {sid: current.get(sid) for sid in subskills})
    for sid in subskills:  # 처음 열 때만 지금 값으로 채운다. 저장하거나 닉네임이 바뀌면 지운다
        st.session_state.setdefault(f"lv_{sid}", baseline[sid])
    st.session_state.setdefault("lv_page", 0)
    page = min(max(st.session_state.lv_page, 0), len(areas) - 1)
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    answered = sum(st.session_state[f"lv_{sid}"] is not None for sid in subskills)
    left = len(subskills) - answered
    minutes = max(1, round(left * SECONDS_PER_QUESTION / 60))
    # 단계 표시: 다 답한 영역은 초록, 지금 보는 영역은 파랑, 나머지는 회색
    steps = []
    for number, (aid, name) in enumerate(areas, start=1):
        ids = [sid for sid, row in subskills.items() if row["area_id"] == aid]
        complete = bool(ids) and all(st.session_state[f"lv_{sid}"] is not None for sid in ids)
        if number - 1 == page:
            steps.append(f":blue-badge[**{number} {name}**]")
        elif complete:
            steps.append(f":green-badge[:material/check: {name}]")
        else:
            steps.append(f":gray-badge[{number} {name}]")
    st.markdown(" → ".join(steps))
    st.caption(f"{answered} / {len(subskills)}개 응답" + (f" · 남은 시간 약 {minutes}분" if left else " · 완료")
               + ". 지금 할 수 있는 일에 가장 가까운 카드를 누르세요. 전부 답하지 않아도 저장할 수 있고, "
               "답하지 않은 항목은 바꾸지 않습니다(저장한 값이 없으면 0단계). 시험이 아니며, 예시는 팀이 확인 중인 AI 초안입니다.")

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
    last = page == len(areas) - 1
    if not last:
        forward.button("다음 영역", key="lvnav_next", type="primary", on_click=_move, args=(1,), width="stretch")
    # 일부만 답해도 저장한다. 답하지 않은 항목은 건드리지 않는다
    changed = {sid: st.session_state[f"lv_{sid}"] for sid in subskills
               if st.session_state[f"lv_{sid}"] is not None and st.session_state[f"lv_{sid}"] != baseline[sid]}
    where = "내 프로필에도 저장합니다." if session else "이번 세션에 저장합니다."
    status.caption(f"<div style='text-align:center'>{where}</div>", unsafe_allow_html=True)
    label = "저장" if not left else f"지금까지 답한 내용 저장 (남은 질문 {left}개)"
    target = forward if last else status
    if target.button(label, type="primary" if last else "secondary", width="stretch", disabled=not changed,
                     key="save_levels"):
        save_levels(session, changed)
        _clear_survey()
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


def clear_chat_level_drafts(keep: set[str] | None = None) -> None:
    """닫힌 문항의 임시 선택이 다음 진단에 다시 나타나지 않게 한다."""
    for key in list(st.session_state):
        if key.startswith("chat_level_") and key != "chat_level_notice" and (not keep or key not in keep):
            del st.session_state[key]


def chat_questions(job: str, questions: dict[str, list[str]], answers: dict[str, int],
                   session: chat_profiles.ProfileSession | None) -> set[str]:
    """기존 수준을 표시하고 변경된 값만 저장한다. 이번 제안에서 닫을 문항 ID를 반환한다."""
    guide = job_guide(job)
    with st.container(border=True):
        st.subheader("관련 역량 진단")
        st.caption(f"{JOBS[job]} 진단에서 지금 할 수 있는 일을 골라 주세요. "
                   "답한 문항의 단계는 공고와 비교할 때 연결된 기술의 수준을 추정하는 데 쓰입니다.")
        changes = {}
        titles = {}
        widgets = {}
        for sid, names in questions.items():
            if sid.startswith(CUSTOM_PREFIX):
                title = f"{names[0]} 직접 입력"
                examples = guide["level_names"]
                help_text = f"공고의 {names[0]} 요구 수준과 비교할 때 이 기술의 직접 입력값을 우선합니다."
            else:
                row = guide["subskills"][sid]
                title = f"{row['name']} ({', '.join(names)})"
                examples = {"0": guide["level_names"]["0"], **row["levels"]}
                help_text = None
            titles[sid] = title
            current = answers.get(sid)
            label = title if current is None else f"{title} · 현재 {current}단계"
            widget_key = f"chat_level_{sid}" if current is None else f"chat_level_{sid}_from_{current}"
            widgets[sid] = widget_key
            labels = {n: f"{n}단계 · {examples[str(n)]}" for n in LEVELS}
            level = st.radio(label, LEVELS, index=current, format_func=labels.__getitem__,
                             key=widget_key, help=help_text)
            if level is not None and level != current:
                changes[sid] = level
        apply, skip = st.columns(2)
        label = "선택한 단계 프로필에 저장" if session else "선택한 단계 이번 세션에 적용"
        if apply.button(label, key="apply_chat_levels", disabled=not changes):
            save_levels(session, changes)
            if (baseline := st.session_state.get(f"lv_initial_{job}")) is not None:
                for sid, value in changes.items():
                    if sid in baseline:
                        baseline[sid] = value
                        st.session_state[f"lv_{sid}"] = value
            if not st.session_state.get("levels_pending"):
                where = "프로필에 저장했습니다." if session else "이번 세션에 반영했습니다."
                confirmed = ", ".join(f"{titles[sid]} {n}단계" for sid, n in changes.items())
                st.session_state.chat_level_notice = f"{confirmed} · {where}"
            finished = changes.keys() | (questions.keys() & answers.keys())
        elif skip.button("건너뛰기", key="skip_chat_levels"):
            finished = set(questions)
        else:
            finished = set()
        clear_chat_level_drafts({key for sid, key in widgets.items() if sid not in finished})
        return finished


def save_levels(session: chat_profiles.ProfileSession | None, chosen: dict[str, int]) -> None:
    """사용자 입력만 세션에 반영하고 바뀐 응답만 계정에 저장한다."""
    current = {} if st.session_state.get("levels_example") else st.session_state.get("levels", {})
    updated = merge_levels(current, chosen)
    _set(updated, False)
    if not session:
        return
    changes = {key: value for key, value in updated.items() if current.get(key) != value}
    _persist_levels(session, changes)


def _persist_levels(session: chat_profiles.ProfileSession, changes: dict[str, int]) -> None:
    """요청된 값만 프로필에 반영하고 손님으로 남겨둔 입력은 화면에 유지한다."""
    pending = {**st.session_state.get("levels_pending", {}), **changes}
    if not pending:
        return
    try:
        saved = chat_profiles.update_profile(session, levels=pending)
    except Exception:  # noqa: BLE001
        logger.warning("프로필 수준을 저장하지 못했습니다")
        st.session_state.levels_pending = pending
        st.session_state.levels_notice = "이번 세션에 반영했습니다. 프로필에 저장하려면 저장을 다시 눌러 주세요."
    else:
        st.session_state.pop("levels_pending", None)
        stored = saved["levels"]
        stored_keys = {key.casefold() for key in stored}
        guest = {key: value for key, value in st.session_state.get("guest_levels", {}).items()
                 if key.casefold() not in stored_keys}
        st.session_state.guest_levels = guest
        _set(merge_levels(guest, stored), False)


def _custom_levels(session: chat_profiles.ProfileSession | None) -> None:
    with st.expander("기술 수준 직접 입력"):
        for name, value in named_levels(st.session_state.levels).items():
            st.text(f"{name} · {value}단계")
        with st.form("custom_level_form"):
            name = st.text_input("기술 이름", key="custom_level_name", placeholder="예: Docker",
                                 max_chars=MAX_CUSTOM_NAME_LENGTH, help=f"한 줄에 {MAX_CUSTOM_NAME_LENGTH}자까지 입력할 수 있습니다.")
            level = st.selectbox("보유 단계", LEVELS, key="custom_level_value",
                                 format_func=lambda n: f"{n} · {level_guide()['level_names'][str(n)]}")
            if st.form_submit_button("기술 수준 저장", key="save_custom_level"):
                try:
                    save_levels(session, {CUSTOM_PREFIX + name: level})
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    st.rerun()


def sidebar_summary(session: chat_profiles.ProfileSession | None, job: str) -> dict[str, int]:
    """사이드바에 고른 직무의 영역별 내 수준을 보여주고 진단 버튼을 둔다. 지금 쓰는 {세부 능력 id: 단계} 를 돌려준다."""
    sync_with_profile(session)
    levels = st.session_state.levels
    guide = job_subskills(job)
    st.markdown("**내 보유 수준**")
    guest = st.session_state.get("guest_levels")
    if session and guest:
        st.caption("로그인 전에 입력한 수준을 이번 세션에서 사용하고 있습니다.")
        if st.button("손님 수준을 내 프로필에 저장", key="save_guest_levels"):
            _persist_levels(session, {key: levels[key] for key in guest})
            st.rerun()
    if st.session_state.get("levels_example"):
        st.caption(f"아직 진단 전이라 예시 값({EXAMPLE_TEXT.get(job, '예시')})으로 보여줍니다.")
    elif not any(levels.get(sid) is not None for sid in guide):
        st.caption("이 직무는 아직 진단하지 않았습니다. 진단하지 않은 세부 능력은 0단계로 봅니다.")
    from app.level_radar import render_level_radar

    render_level_radar(job, levels)  # 영역별 내 단계와 기준 단계를 레이더 차트로 보여 준다
    st.caption("수준 진단은 학습 상담 화면의 '수준 진단 설문'에서 합니다.")
    _custom_levels(session)
    notice = st.session_state.pop("levels_notice", None)
    if notice:
        st.warning(notice)
    if session and st.session_state.get("levels_pending") and st.button("프로필 저장 다시 시도", key="retry_levels"):
        _persist_levels(session, {})
        st.rerun()
    return dict(levels)


def survey_button(session: chat_profiles.ProfileSession | None, job: str) -> None:
    """학습 상담 화면에 두는 수준 진단 설문 버튼. 누르면 그 직무의 설문 창이 열린다."""
    if st.button("수준 진단 설문", key="open_levels", icon=":material/checklist:", width="stretch",
                 help="세부 능력마다 지금 할 수 있는 일을 고릅니다. 일부만 답해도 저장됩니다."):
        diagnose_dialog(session, job)


def chat_skills(job: str, levels: dict[str, int]) -> list[str]:
    """상담 채팅에 넘길 보유 기술 이름. 예시 값은 넘기지 않는다(사용자가 고른 값만 보유로 본다)."""
    if st.session_state.get("levels_example") or not levels:
        return []
    from app.common import load
    market_owned, _ = load(api.owned_skill_names, job, levels)
    return chat_owned_skill_names(job, levels, market_owned or [])
