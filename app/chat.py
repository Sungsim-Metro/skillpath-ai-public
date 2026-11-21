"""학습 상담 화면과 세션 안에서만 유지하는 대화 표시 기록.

답마다 좋아요·별로예요 평가를 받고, 과정 추천이 두 가지 방식으로 왔으면 A/B 로 나란히 보여 주고 선택을 받는다
(비교 실험, backend/experiments.py). 어떤 모델·구조인지는 화면에 보여 주지 않는다. 기록에는 내용을 넣지 않는다.
처음 접속하면 로그인·회원가입 화면(entry_gate)을 먼저 보여 주고, 비회원으로 둘러볼 수도 있다(2026-11-20).
부족 역량이나 과정 추천을 답하면 답 아래에 해당 화면으로 가는 버튼을 둔다(2026-11-20). 과정 카드는 app/course_card.py 가 그린다.
토큰 수는 상담 화면에 보여 주지 않는다(관리자 화면에서 본다). 평가와 A/B 선택은 한 번 고르면 감사 인사를 띄우고 숨긴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import TYPE_CHECKING

import streamlit as st

from ai.usage import SessionUsage
from app import levels as levels_screen
from app import ui
from app.course_card import render_course_list
from backend import chat_history, chat_profiles
from backend.jobs import JOBS
from backend.levels import assessment_questions, validate_levels
from pipeline.aliases import skill_aliases

if TYPE_CHECKING:
    from backend.chat import Conversation, Reply


@lru_cache(maxsize=1)
def skill_options() -> tuple[str, ...]:
    """채팅 입장 시 수요 조회 없이 고를 수 있는 기술 대표 이름."""
    return tuple(skill_aliases())


# 로그아웃해도 남기는 값: 보던 화면과 '첫 화면을 지났다'는 표시(로그아웃하면 비회원으로 계속 본다)
KEPT_AFTER_LOGOUT = ("view", "entered", "tour_hidden")


def _clear_private_state() -> None:
    """Clear account data and widget drafts before the next screen renders."""
    for key in list(st.session_state):
        if key not in KEPT_AFTER_LOGOUT:
            del st.session_state[key]


def _authenticate(register: bool) -> None:
    nickname = st.session_state.auth_nickname
    password = st.session_state.auth_password
    st.session_state.auth_password = ""
    authenticate = chat_profiles.create_profile if register else chat_profiles.sign_in
    try:
        session = authenticate(nickname, password)
    except chat_profiles.ProfileAuthError as exc:
        st.session_state.auth_error = str(exc)
    except Exception:
        st.session_state.auth_error = "프로필에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."
    else:
        st.session_state.pop("auth_error", None)
        st.session_state.pop("chat_assessment", None)
        st.session_state.pop("chat_level_notice", None)
        st.session_state.profile_session = session


def _enter_as_guest() -> None:
    st.session_state.entered = True


def entry_gate() -> None:
    """처음 접속했을 때의 로그인·회원가입 화면. 로그인하거나 비회원으로 들어오기 전에는 여기서 멈춘다.

    확인은 사이드바 로그인과 같은 `_authenticate` 가 한다(backend.chat_profiles 의 sign_in, create_profile).
    비회원은 모든 화면을 보지만 프로필을 저장하거나 불러오지 않는다. 한 번 지나면 로그아웃해도 다시 막지 않는다.
    """
    if st.session_state.get("entered") or st.session_state.get("profile_session") is not None:
        if st.session_state.pop("gate_shown", False) and "tour_hidden" not in st.session_state:
            st.session_state.tour_open = True  # 방금 첫 화면을 지나 들어왔으면 사용 안내 창을 한 번 연다
        st.session_state.entered = True
        return
    st.session_state.gate_shown = True
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title("skillpath-ai")
        st.markdown(PITCH)  # 처음 보는 화면이라 무엇을 해 주는지와 쓰는 순서를 회색 글씨가 아닌 본문으로 적는다
        st.markdown("① 해 본 일을 말하면 → ② 공고와 비교해 부족한 기술을 찾고 → ③ 들을 과정을 추천합니다.")
        with st.form("entry_access"):
            st.text_input("닉네임", key="auth_nickname")
            st.text_input("비밀번호", type="password", key="auth_password",
                          help="앞뒤 공백도 비밀번호에 포함됩니다.")
            login, register = st.columns(2)
            login.form_submit_button("로그인", key="entry_login", on_click=_authenticate, args=(False,),
                                     type="primary", width="stretch")
            register.form_submit_button("회원가입", key="entry_register", on_click=_authenticate, args=(True,),
                                        width="stretch")
        if error := st.session_state.get("auth_error"):
            st.error(error)
        st.caption("처음이면 닉네임과 비밀번호를 적고 회원가입을 누릅니다. 다른 서비스에서 쓰는 비밀번호는 쓰지 마세요.")
        st.button("로그인 없이 둘러보기", key="guest_enter", on_click=_enter_as_guest, width="stretch")
        st.caption("비회원은 진단 결과와 대화가 저장되지 않습니다.")
    st.stop()


def profile_access() -> chat_profiles.ProfileSession | None:
    """Authenticate a profile without sending credentials to the chat."""
    if st.session_state.pop("chat_identity", ""):
        # Open sessions from the nickname-only UI must authenticate again.
        _clear_private_state()
    session = st.session_state.get("profile_session")
    if session is not None:
        st.text(f"로그인한 프로필 · {session.nickname}")
        st.button("로그아웃", key="auth_logout", on_click=_clear_private_state)
        return session

    # 비회원에게는 접어 둔다(메뉴와 보유 수준이 폼에 밀리지 않게). 오류가 있으면 펼쳐서 보여 준다
    error = st.session_state.get("auth_error")
    with st.expander("로그인 · 회원가입", expanded=bool(error), icon=":material/person:"):
        with st.form("profile_access", border=False):
            st.text_input("닉네임", key="auth_nickname")
            st.text_input("비밀번호", type="password", key="auth_password",
                          help="앞뒤 공백도 비밀번호에 포함됩니다.")
            login, register = st.columns(2)
            login.form_submit_button("로그인", key="auth_login", on_click=_authenticate, args=(False,),
                                     type="primary", width="stretch")
            register.form_submit_button("회원가입", key="auth_register", on_click=_authenticate, args=(True,),
                                        width="stretch")
        if error:
            st.error(error)
    return None


# 답의 요청 종류(backend.chat.Reply.action) → (옮겨 갈 화면, 버튼 글자). 화면 값은 app/main.py 의 VIEWS 와 같다
SHORTCUTS = {"gap": ("gap", "부족 역량 자세히 보기"), "courses": ("recommend", "추천 과정 보기")}


# 상담 화면에 늘 보여 주는 추천 질문 (버튼 글자, 보낼 메시지). 좁은 화면에서도 잘리지 않게 버튼 글자는 짧게 둔다.
# 마지막 칸은 지금 목표가 아닌 직무로 바꿔 보는 질문이다(_suggestions 가 붙인다).
SUGGESTIONS = (("내 경험 정리", "내 경험을 정리해줘"), ("부족한 기술", "지금 뭐가 부족해?"),
               ("교육과정 추천", "먼저 들을 교육과정을 추천해줘"))
JOB_SHORT = {"cloud_engineer": "클라우드", "ai_engineer": "AI"}  # 직무 전환 버튼의 글자
THANKS = "의견 주셔서 감사합니다!"


def _go(view: str) -> None:
    """사이드바 메뉴의 화면을 바꾼다. 메뉴가 이미 그려진 뒤에는 값을 바꿀 수 없어 버튼의 on_click 으로만 부른다.

    상담에서 넘어갔다는 표시를 남겨, 넘어간 화면에 상담으로 돌아가는 버튼을 보여 준다(app/main.py).
    """
    st.session_state.view = view
    if view == "chat":
        st.session_state.pop("from_chat", None)
    else:
        st.session_state.from_chat = view  # 넘어간 그 화면에서만 돌아가기 버튼을 보여 준다


def back_to_chat() -> None:
    _go("chat")


def _suggestions(target: str) -> tuple[tuple[str, str], ...]:
    """추천 질문 넷. 마지막은 지금 보고 있는 직무가 아닌 다른 직무로 바꾸는 버튼이다(직무가 둘이라 서로 바뀐다)."""
    other = next((job for job in JOBS if job != target), None)
    if other is None:
        return SUGGESTIONS
    return SUGGESTIONS + ((f"{JOB_SHORT.get(other, JOBS[other])} 직무로 보기", f"{JOBS[other]} 기준으로 다시 봐줘"),)


def _suggest(text: str) -> None:
    st.session_state.chat_suggested = text


def _pick_job(job: str | None) -> None:
    """상담 첫 화면에서 목표 직무를 고른다. None 은 '아직 모르겠어요'(기본 직무로 두고 안내만 한다)."""
    if job in JOBS:
        st.session_state.job = job
    st.session_state.job_picked = job or "unknown"


def _job_picker() -> None:
    """아직 직무를 고르지 않았고 대화도 시작하지 않았을 때만 보여 준다. 고른 뒤에도 대화로 바꿀 수 있다."""
    with st.container(border=True):
        st.markdown("**어떤 직무를 준비하세요?**")
        names = list(JOBS)
        for column, job in zip(st.columns(len(names) + 1), [*names, None]):
            column.button(JOBS[job] if job else "아직 모르겠어요", key=f"pick_job_{job or 'unknown'}",
                          on_click=_pick_job, args=(job,), width="stretch",
                          type="primary" if job else "secondary")
        st.caption("고른 직무의 채용공고로 부족한 역량을 봅니다. 나중에 '클라우드 엔지니어 기준으로 봐줘'처럼 말하면 바뀝니다.")


def _reset_chat() -> None:
    """새 상담: 대화와 진행 중인 수준 문항만 비운다. 로그인, 진단 결과, 프로필은 그대로다."""
    for key in ("chat_conversation", "chat_history", "chat_assessment", "chat_suggested", "viewing_session"):
        st.session_state.pop(key, None)
    levels_screen.clear_chat_level_drafts()


def _save(entry: dict, **fields) -> bool:
    """평가 한 건을 기록하고 성공했는지 돌려준다. 기록 실패는 상담을 막지 않고 한 줄로만 알린다."""
    from backend import experiments

    try:
        experiments.save_feedback(entry["session_id"], entry["turn_id"], job=entry["job"],
                                  action=entry["action"] or None, model=entry["model"] or None, **fields)
    except Exception:
        st.toast("평가를 저장하지 못했습니다. 다시 눌러 주세요. 상담은 계속할 수 있습니다.")
        return False
    return True


def _on_rating(entry: dict) -> None:
    value = st.session_state.get(f"fb_{entry['turn_id']}")
    if value is not None:  # 썸 업 1, 썸 다운 0
        if _save(entry, rating="up" if value == 1 else "down"):  # 저장하지 못했으면 다시 누를 수 있게 남겨 둔다
            entry["rated"] = True  # 한 번 고르면 평가 위젯을 숨긴다
            st.toast(THANKS)


def _on_pick(entry: dict, choice: str) -> None:
    """A/B 선택. choice 는 "A", "B", "tie"."""
    from backend import experiments

    left, right = entry["alternatives"]
    picked = {"A": left["structure"], "B": right["structure"], "tie": experiments.TIE}.get(choice)
    if picked and _save(entry, left=left["structure"], right=right["structure"], picked=picked,
                        tokens={side["structure"]: side["tokens"] for side in (left, right)}):
        entry["picked"] = choice  # 한 번 고르면 선택 버튼을 숨긴다(저장하지 못했으면 남겨 둔다)
        st.toast(THANKS)


def _render_answer(entry: dict, index: int) -> None:
    from backend import experiments

    st.text(entry["answer"])
    if len(entry["alternatives"]) == 2:
        chosen = entry.get("picked")
        if not chosen:
            st.caption("두 가지 추천 가운데 더 도움이 되는 쪽을 골라 주세요.")
        for column, name, side in zip(st.columns(2), ("A", "B"), entry["alternatives"]):
            with column:
                st.markdown(f"**추천 {name}**")
                if not chosen:  # 카드 묶음 바로 위의 버튼으로 그쪽을 고른다
                    st.button(f"추천 {name} 고르기", key=f"ab_{entry['turn_id']}_{name}", type="primary",
                              width="stretch", on_click=_on_pick, args=(entry, name))
                elif chosen == name:
                    st.caption(":material/check_circle: 이 추천을 골랐습니다.")
                render_course_list(side["courses"], key=f"chat{index}{name}")
        if not chosen:
            st.button("둘이 비슷해요", key=f"ab_{entry['turn_id']}_tie", on_click=_on_pick, args=(entry, "tie"))
        elif chosen == "tie":
            st.caption(":material/check_circle: 둘이 비슷하다고 골랐습니다.")
    else:
        render_course_list(entry["courses"], key=f"chat{index}")
    action = entry.get("action")  # 평가 기능 이전의 열린 세션 기록에는 요청 종류가 없다
    shortcut = SHORTCUTS.get(action)
    if shortcut and (action == "gap" or entry["courses"] or entry["alternatives"]):
        view, label = shortcut
        st.button(label, key=f"go_{index}", on_click=_go, args=(view,), icon=":material/arrow_forward:")
    if (entry["turn_id"] and entry.get("architecture") != "supervised"
            and experiments.active() and not entry.get("rated")):
        st.feedback("thumbs", key=f"fb_{entry['turn_id']}", on_change=_on_rating, args=(entry,))


KST = timezone(timedelta(hours=9))
PANEL_REM = 17
# 패널이 열려 있을 때만 쓴다. 고정 문자열이라 사용자 입력이 들어가지 않는다.
# - 패널 열: 너비를 rem 으로 고정한다. 패널은 화면 높이만큼 길게(입력창 높이까지) 늘리고 스크롤해도 따라오게 sticky 로 둔다.
#   Streamlit 이 붙이는 height:auto 보다 이기려고 선택자에 .stVerticalBlock 을 함께 쓰고, flex-basis 가 0% 면 height 가 무시되므로 flex: 0 0 auto 로 둔다.
#   아래쪽 음수 여백은 상자는 입력창 높이까지 보이되 페이지 전체는 화면보다 길어지지 않게(스크롤 방지) 한다.
#   z-index 100 은 화면 아래 고정 입력창 영역(99) 위로 올려 상자 테두리가 입력창 높이까지 끊기지 않게 한다.
# - 입력창: 화면 아래에 고정된 입력창을 패널 너비만큼 오른쪽으로 민다(접으면 이 CSS 를 쓰지 않아 전체 폭이 된다).
PANEL_CSS = f"""<style>
/* 열 두 개가 한 줄에 못 들어가면(Streamlit 버전에 따라) 본문이 패널 아래로 밀려 제목을 가린다. 줄바꿈을 끄고 본문 열이 남는 폭을 쓰게 한다 */
[data-testid="stHorizontalBlock"]:has(.st-key-history_panel) {{ flex-wrap: nowrap; }}
[data-testid="stHorizontalBlock"]:has(.st-key-history_panel) > [data-testid="stColumn"]:not(:has(.st-key-history_panel)) {{ flex: 1 1 0; min-width: 0; width: auto; }}
[data-testid="stColumn"]:has(.st-key-history_panel) {{ flex: 0 0 {PANEL_REM}rem; width: {PANEL_REM}rem; min-width: {PANEL_REM}rem; align-self: stretch; }}
.stVerticalBlock.st-key-history_panel {{ flex: 0 0 auto; position: sticky; top: 4rem; height: calc(100vh - 8.5rem); margin-bottom: -7.5rem; z-index: 100; overflow-y: auto; background: #ffffff;
  border: 1px solid rgba(49, 51, 63, 0.2); border-radius: 0.5rem; padding: 1rem; }}
.st-key-chat_message {{ margin-left: {PANEL_REM + 1}rem; width: calc(100% - {PANEL_REM + 1}rem); }}
</style>"""
PITCH = "**실제 채용공고가 요구하는 기술과 내 경험을 비교해, 부족한 기술과 들을 AWS 교육과정을 알려 드립니다.**"
# 무엇을 적을지 몰라 멈추는 사용자가 있어 예시 문장을 보여 준다(설문 2026-11-20)
EXAMPLE_INPUT = "예: “학교 프로젝트에서 EC2에 서버를 올리고 GitHub Actions로 배포해 봤어요.”"
GUEST_NOTICE = "비회원의 대화는 저장되지 않습니다. 왼쪽에서 로그인하면 대화가 저장됩니다."
MEMBER_NOTICE = ("로그인했으므로 이 대화는 저장되고 '대화 기록'의 최근 목록에서 다시 볼 수 있습니다. "
                 "이름·연락처·주소 같은 개인정보는 입력하지 마세요. 연락처 모양은 가려서 저장하지만 이름과 주소는 가릴 수 없습니다.")


def _when(iso: str) -> str:
    """저장 시각(UTC)을 한국 시간 '월/일 시:분'으로 보인다."""
    return datetime.fromisoformat(iso).astimezone(KST).strftime("%m/%d %H:%M")


def _current_session_id() -> str | None:
    conversation = st.session_state.get("chat_conversation")
    return getattr(getattr(conversation, "usage", None), "session_id", None)


def _open_past(session_id: str) -> None:
    if session_id == _current_session_id():  # 지금 하고 있는 대화를 누르면 그대로 돌아온다
        st.session_state.pop("viewing_session", None)
    else:
        st.session_state.viewing_session = session_id


def _back_to_current() -> None:
    st.session_state.pop("viewing_session", None)


def _toggle_history(opened: bool) -> None:
    st.session_state.history_open = not opened


def _toggle_button(opened: bool) -> None:
    """패널을 접고 펼치는 버튼. 새 채팅 버튼과 같은 글자·아이콘 크기를 쓴다."""
    st.button("대화 기록 접기" if opened else "대화 기록 펼치기", key="history_toggle", on_click=_toggle_history, args=(opened,),
              icon=":material/left_panel_close:" if opened else ":material/left_panel_open:",
              width="stretch" if opened else "content")


def _new_chat_button(container=st) -> None:
    """대화만 비우는 새 채팅 버튼. 패널 안과 제목 옆(패널이 없거나 접혔을 때)이 같은 버튼이고, 둘이 함께 나오지는 않는다."""
    container.button("새 채팅", key="chat_new", on_click=_reset_chat, icon=":material/edit_square:", width="stretch",
                     help="대화만 비웁니다. 로그인과 진단 결과는 그대로입니다.")


def _history_panel(session: chat_profiles.ProfileSession | None) -> None:
    """학습 상담 왼쪽 패널: 새 채팅 버튼과 '최근' 대화 목록. 누르면 그 대화를 읽기 전용으로 연다(이어 쓰기는 아직 없다)."""
    _toggle_button(True)
    _new_chat_button()
    st.caption("최근")
    if session is None:
        st.caption("로그인하면 대화 기록이 저장됩니다.")
        return
    try:
        sessions = chat_history.list_sessions(session.nickname)
    except Exception:  # 예외에 닉네임이나 대화가 섞일 수 있어 문구만 보인다
        st.caption("대화 기록을 불러오지 못했습니다.")
        return
    if not sessions:
        st.caption("저장된 대화가 아직 없습니다.")
        return
    shown = st.session_state.get("viewing_session") or _current_session_id()
    for item in sessions:
        st.button(item["title"], key=f"history_{item['session_id']}", help=_when(item["updated_at"]),
                  on_click=_open_past, args=(item["session_id"],), width="stretch",
                  type="secondary" if item["session_id"] == shown else "tertiary")


CHAT_HEIGHT = 520  # 대화 목록 상자의 높이(px). 이보다 길어지면 상자 안에서 스크롤한다


def _scroll_area(has_content: bool):
    """대화 목록을 담는 상자. 비어 있을 때는 높이를 고정하지 않아 빈 칸이 크게 남지 않게 한다."""
    return st.container(height=CHAT_HEIGHT, border=False) if has_content else st.container()


def _render_entries(entries: list) -> None:
    for index, entry in enumerate(entries):
        if isinstance(entry, tuple):  # 평가 기능 이전의 열린 세션에는 턴 ID가 없다.
            prompt, answer, courses = entry
            entry = {"prompt": prompt, "answer": answer, "courses": courses, "alternatives": [], "turn_id": ""}
        with st.chat_message("user"):
            st.text(entry["prompt"])
        with st.chat_message("assistant"):
            _render_answer(entry, index)


def _render_past(nickname: str, session_id: str) -> None:
    st.info("지난 대화를 보고 있습니다. 새 질문을 입력하면 현재 대화로 돌아갑니다.")
    st.button("현재 대화로 돌아가기", key="history_back", on_click=_back_to_current)
    try:
        turns = chat_history.load_session(nickname, session_id)
    except Exception:
        st.caption("이 대화를 불러오지 못했습니다.")
        return
    with _scroll_area(bool(turns)):
        _render_entries([{"prompt": q, "answer": a, "courses": c, "alternatives": [], "turn_id": ""}
                         for q, a, c in turns])


def _assessment_panel(conversation: Conversation, sidebar_job: str, session: chat_profiles.ProfileSession | None,
                      levels: dict[str, int] | None, reply: Reply | None) -> None:
    """새 사용 진술은 기응답 문항도 다시 열고, 화면 재표시는 진행 중인 문항만 유지한다."""
    if notice := st.session_state.pop("chat_level_notice", None):
        st.success(notice)
    profile = conversation.profile or {}
    job = conversation.job or profile.get("job") or sidebar_job
    pending = st.session_state.get("chat_assessment", ())
    same_job = bool(pending and pending[0] == job)
    previous = pending[1] if same_job else []
    # 예전 세션에는 직무와 기술 목록만 들어 있다.
    closed = set(pending[2]) if same_job and len(pending) > 2 else set()
    mentioned = reply.assessment_skills if getattr(reply, "assessment_job", "") == job else []
    skills = list(dict.fromkeys(name for name in [*previous, *mentioned] if conversation.skills.get(name)))
    answers = validate_levels(levels if levels is not None else profile.get("levels", {}))
    questions = {}
    if skills and job in JOBS and not conversation.needs_role:
        if closed and mentioned:
            closed.difference_update(assessment_questions(mentioned, job, answers))
        questions = {key: names for key, names in assessment_questions(skills, job, answers).items()
                     if key not in closed}
    if not same_job or not questions:
        levels_screen.clear_chat_level_drafts()
    if not questions:
        st.session_state.pop("chat_assessment", None)
        return
    finished = levels_screen.chat_questions(job, questions, answers, session)
    st.session_state.chat_assessment = (job, skills, closed | finished)
    if finished:
        st.rerun()


def _render_current_chat(chat, conversation, history: list, message: str | None, *, job: str,
                         sidebar_skills: list[str], session: chat_profiles.ProfileSession | None,
                         sidebar_levels: dict[str, int] | None, nickname: str | None, architecture: str) -> None:
    """지금 하고 있는 대화: 내 상태, 대화 목록, 새 답, 수준 문항, 설문과 추천 질문."""
    # 내 상태 한 줄: 목표 직무, 진단으로 확인한 보유 기술, 이번 대화에서 말한 기술
    target = conversation.job or (conversation.profile or {}).get("job") or job
    mentioned = sum(1 for used in conversation.skills.values() if used)
    diagnosed = f"진단으로 확인한 보유 기술 {len(sidebar_skills)}개" if sidebar_levels is not None else "수준 진단 전"
    st.caption(f":material/flag: 목표 직무 {JOBS.get(target, target)} · {diagnosed} · 대화에서 말한 기술 {mentioned}개")
    picked = st.session_state.get("job_picked")
    if not picked and not history and not message:
        _job_picker()
    elif picked == "unknown" and not history:
        st.info(f"우선 {JOBS[job]} 기준으로 보여 드립니다. 해 본 일을 적어 주시면 상담에서 맞는 직무를 함께 찾아볼 수 있습니다.",
                icon=":material/explore:")
    target_job = target if target in JOBS else job
    fresh = not history and not (message and message.strip())  # 대화 전에는 할 일을 위에서 먼저 보여 준다
    if fresh:
        _suggestion_bar(session, target_job)

    reply = None
    # 대화가 화면보다 길어지면 아래로 늘어나지 않고 이 상자 안에서 스크롤한다(새 메시지가 오면 맨 아래로 따라간다)
    with _scroll_area(bool(history) or bool(message and message.strip())):
        for index, entry in enumerate(history):
            if isinstance(entry, tuple):  # 평가 기능 이전의 열린 세션에는 턴 ID가 없다.
                prompt, answer, courses = entry
                entry = {"prompt": prompt, "answer": answer, "courses": courses, "alternatives": [], "turn_id": ""}
            with st.chat_message("user"):
                st.text(entry["prompt"])
            with st.chat_message("assistant"):
                _render_answer(entry, index)

        if message and message.strip():
            with st.chat_message("user"):
                st.text(message)
            with st.chat_message("assistant"):
                with st.spinner("답을 준비하고 있습니다… 교육과정 추천은 시간이 조금 더 걸립니다."):  # 답이 올 때까지 말풍선 안에 진행 표시를 둔다
                    try:
                        reply = chat.handle_message(
                            conversation, message,
                            job=job, sidebar_skills=sidebar_skills, session=session, sidebar_levels=sidebar_levels,
                            architecture=architecture,
                        )
                        # 실험용 항목은 없을 수도 있다(없으면 평가 위젯을 붙이지 않는다)
                        entry = {"answer": reply.text, "courses": reply.courses,
                                 "turn_id": getattr(reply, "turn_id", ""), "model": getattr(reply, "model", ""),
                                 "action": getattr(reply, "action", ""),
                                 "alternatives": getattr(reply, "alternatives", [])}
                    except Exception:
                        # 예외에 닉네임, 프로필 또는 원문이 포함될 수 있다.
                        entry = {"answer": "메시지 처리 결과를 확인할 수 없습니다.", "courses": [], "turn_id": "",
                                 "model": "", "action": "", "alternatives": []}
                history.append({"prompt": message, "job": job, "session_id": conversation.usage.session_id,
                                "architecture": architecture, **entry})
                chat_history.record_reply(nickname, conversation.usage.session_id, message, reply)  # 저장 여부는 backend 가 정한다
                _render_answer(history[-1], len(history) - 1)
            # 대화에서 목표 직무가 정해지거나 바뀌면 마이페이지와 다른 화면도 그 직무를 따른다
            if conversation.job in JOBS and conversation.job != st.session_state.get("job"):
                st.session_state.job = conversation.job
                st.session_state.job_picked = conversation.job
                # 다시 그리면 이번 답(reply)이 사라지므로, 이번 턴에 말한 기술의 수준 문항을 먼저 맡겨 둔다
                mentioned = list(getattr(reply, "assessment_skills", None) or [])
                if mentioned and getattr(reply, "assessment_job", "") == conversation.job:
                    st.session_state.chat_assessment = (conversation.job, mentioned, set())
                st.rerun()
    _assessment_panel(conversation, job, session, sidebar_levels, reply)

    if not fresh:
        _suggestion_bar(session, target_job)
    if any(isinstance(entry, dict) and (entry.get("courses") or entry.get("alternatives")) for entry in history):
        ui.survey_card()  # 과정 추천까지 받았으면 설문으로 안내한다


def _suggestion_bar(session: chat_profiles.ProfileSession | None, job: str) -> None:
    """수준 진단 설문 버튼과 추천 질문. 대화 전에는 대화 위에, 대화가 시작되면 맨 아래에 그린다."""
    survey, _ = st.columns([1, 3])
    with survey:
        levels_screen.survey_button(session, job)
    st.caption("이렇게 물어보세요")
    choices = _suggestions(job)
    for number, (column, (label, text)) in enumerate(zip(st.columns(len(choices)), choices)):
        column.button(label, key=f"suggest_{number}", on_click=_suggest, args=(text,), width="stretch", help=text)


def render(job: str, sidebar_skills: list[str], session: chat_profiles.ProfileSession | None,
           sidebar_levels: dict[str, int] | None = None, *, architecture: str = "screen") -> None:
    from backend import chat

    st.session_state.pop("from_chat", None)  # 상담으로 돌아왔으면 '돌아가기' 표시를 지운다
    nickname = session.nickname if session else None
    history_on = chat_history.enabled()  # 저장소가 준비되지 않았으면 패널도 '저장됩니다' 안내도 그리지 않는다
    # 비회원은 저장된 대화가 없어 빈 패널만 화면을 차지한다. 회원만 처음부터 펼쳐 둔다(설문 2026-11-20)
    opened = history_on and st.session_state.get("history_open", nickname is not None)
    panel, body = st.columns([1, 4]) if opened else (None, st.container())  # 패널은 메시지를 처리한 뒤 채운다
    with body:
        if history_on and not opened:
            _toggle_button(False)
        heading, action = st.columns([4, 1], vertical_alignment="bottom")
        heading.title("학습 상담")
        if not opened:  # 패널이 열려 있으면 패널의 '새 채팅'을 쓴다. 패널이 없거나 접혔을 때 대화를 비울 길을 남긴다
            _new_chat_button(action)
        else:  # 좁은 화면에서는 패널이 숨겨져 새 채팅 버튼도 사라진다. 그때만 보이는 버튼을 둔다(app/ui.py 의 좁은 화면 규칙)
            action.button("새 채팅", key="chat_new_narrow", on_click=_reset_chat, icon=":material/edit_square:", width="stretch")
        st.markdown(PITCH)  # 처음 온 사람이 무엇을 받는지 먼저 알게 한다
        st.caption("해 본 일을 편하게 적어 주세요. " + EXAMPLE_INPUT)
        if history_on:
            st.caption(MEMBER_NOTICE if nickname else GUEST_NOTICE)
    if "chat_conversation" not in st.session_state:
        st.session_state.chat_conversation = chat.Conversation()
        st.session_state.chat_history = []
    conversation = st.session_state.chat_conversation
    if not hasattr(conversation, "usage"):
        conversation.usage = SessionUsage()
    history = st.session_state.chat_history
    # 저장되는 회원에게는 입력하는 자리에서 직접 알린다(제목 아래 안내문은 놓치기 쉽다)
    placeholder = "해 본 일이나 궁금한 점을 적어 주세요." + (" 이름·연락처는 적지 마세요." if history_on and nickname else "")
    typed = st.chat_input(placeholder, key="chat_message")
    message = typed or st.session_state.pop("chat_suggested", None)  # 추천 질문 버튼도 같은 길로 들어온다
    if message and message.strip():
        st.session_state.pop("viewing_session", None)  # 새 질문은 현재 대화에 이어진다
    viewing = st.session_state.get("viewing_session")
    with body:
        if nickname and viewing:
            _render_past(nickname, viewing)
        else:
            _render_current_chat(chat, conversation, history, message, job=job, sidebar_skills=sidebar_skills,
                                 session=session, sidebar_levels=sidebar_levels, nickname=nickname,
                                 architecture=architecture)
    # 패널은 메시지를 처리하고 저장한 뒤에 채운다. 그래야 방금 보낸 첫 대화도 목록에 바로 뜬다.
    if opened:
        with panel, st.container(key="history_panel"):
            _history_panel(session)
        st.markdown(PANEL_CSS, unsafe_allow_html=True)  # 빈 요소가 남는 자리가 화면 맨 끝이 되도록 마지막에 둔다
