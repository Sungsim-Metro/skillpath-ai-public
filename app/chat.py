"""학습 상담 화면과 세션 안에서만 유지하는 대화 표시 기록.

답마다 좋아요·별로예요 평가를 받고, 과정 추천이 두 가지 방식으로 왔으면 A/B 로 나란히 보여 주고 선택을 받는다
(비교 실험, backend/experiments.py). 어떤 모델·구조인지는 화면에 보여 주지 않는다. 기록에는 내용을 넣지 않는다.
처음 접속하면 로그인·회원가입 화면(entry_gate)을 먼저 보여 주고, 비회원으로 둘러볼 수도 있다(2026-11-20).
부족 역량이나 과정 추천을 답하면 답 아래에 해당 화면으로 가는 버튼을 둔다(2026-11-20). 과정 카드는 app/course_card.py 가 그린다.
"""
from __future__ import annotations

from functools import lru_cache

import streamlit as st

from ai.usage import SessionUsage
from app.course_card import render_course_list
from backend import chat_profiles
from pipeline.aliases import skill_aliases


@lru_cache(maxsize=1)
def skill_options() -> tuple[str, ...]:
    """채팅 입장 시 수요 조회 없이 고를 수 있는 기술 대표 이름."""
    return tuple(skill_aliases())


# 로그아웃해도 남기는 값: 보던 화면과 '첫 화면을 지났다'는 표시(로그아웃하면 비회원으로 계속 본다)
KEPT_AFTER_LOGOUT = ("view", "entered")


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
        st.session_state.profile_session = session


def _enter_as_guest() -> None:
    st.session_state.entered = True


def entry_gate() -> None:
    """처음 접속했을 때의 로그인·회원가입 화면. 로그인하거나 비회원으로 들어오기 전에는 여기서 멈춘다.

    확인은 사이드바 로그인과 같은 `_authenticate` 가 한다(backend.chat_profiles 의 sign_in, create_profile).
    비회원은 모든 화면을 보지만 프로필을 저장하거나 불러오지 않는다. 한 번 지나면 로그아웃해도 다시 막지 않는다.
    """
    if st.session_state.get("entered") or st.session_state.get("profile_session") is not None:
        st.session_state.entered = True
        return
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title("skillpath-ai")
        st.caption("채용공고에서 요구 기술을 찾아 부족한 역량과 AWS 교육과정을 추천합니다. "
                   "진단 결과와 상담에서 말한 경험을 이어서 보려면 로그인하세요.")
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
        st.caption("처음이면 닉네임과 비밀번호를 적고 회원가입을 누릅니다. 이름이나 연락처는 받지 않습니다. "
                   "다른 서비스에서 쓰는 비밀번호는 쓰지 마세요.")
        st.button("로그인 없이 둘러보기", key="guest_enter", on_click=_enter_as_guest, width="stretch")
        st.caption("비회원도 상담과 모든 화면을 볼 수 있습니다. 진단 결과와 상담 정보는 저장되지 않고 창을 닫으면 사라집니다. "
                   "들어간 뒤에도 사이드바에서 로그인할 수 있습니다.")
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

    with st.form("profile_access"):
        st.text_input("닉네임", key="auth_nickname")
        st.text_input("비밀번호", type="password", key="auth_password",
                      help="앞뒤 공백도 비밀번호에 포함됩니다.")
        login, register = st.columns(2)
        login.form_submit_button("로그인", key="auth_login", on_click=_authenticate, args=(False,))
        register.form_submit_button("새 프로필 만들기", key="auth_register", on_click=_authenticate, args=(True,))
    if error := st.session_state.get("auth_error"):
        st.error(error)
    return None


# 답의 요청 종류(backend.chat.Reply.action) → (옮겨 갈 화면, 버튼 글자). 화면 값은 app/main.py 의 VIEWS 와 같다
SHORTCUTS = {"gap": ("gap", "부족 역량 자세히 보기"), "courses": ("recommend", "추천 과정 보기")}


def _go(view: str) -> None:
    """사이드바 메뉴의 화면을 바꾼다. 메뉴가 이미 그려진 뒤에는 값을 바꿀 수 없어 버튼의 on_click 으로만 부른다."""
    st.session_state.view = view


def _save(entry: dict, **fields) -> None:
    """평가 한 건을 기록한다. 기록 실패는 상담을 막지 않고 한 줄로만 알린다."""
    from backend import experiments

    try:
        experiments.save_feedback(entry["session_id"], entry["turn_id"], job=entry["job"],
                                  action=entry["action"] or None, model=entry["model"] or None, **fields)
    except Exception:
        st.toast("평가를 저장하지 못했습니다. 상담은 계속할 수 있습니다.")


def _on_rating(entry: dict) -> None:
    value = st.session_state.get(f"fb_{entry['turn_id']}")
    if value is not None:  # 썸 업 1, 썸 다운 0
        _save(entry, rating="up" if value == 1 else "down")


def _on_pick(entry: dict) -> None:
    from backend import experiments

    choice = st.session_state.get(f"ab_{entry['turn_id']}")
    left, right = entry["alternatives"]
    picked = {"A": left["structure"], "B": right["structure"], "비슷해요": experiments.TIE}.get(choice)
    if picked:
        _save(entry, left=left["structure"], right=right["structure"], picked=picked,
              tokens={side["structure"]: side["tokens"] for side in (left, right)})


def _render_answer(entry: dict, index: int) -> None:
    from backend import experiments

    st.text(entry["answer"])
    if len(entry["alternatives"]) == 2:
        for column, label, side in zip(st.columns(2), ("추천 A", "추천 B"), entry["alternatives"]):
            with column:
                st.markdown(f"**{label}**")
                render_course_list(side["courses"])
        st.radio("어느 쪽 추천이 더 도움이 되나요?", ["A", "B", "비슷해요"], index=None, horizontal=True,
                 key=f"ab_{entry['turn_id']}", on_change=_on_pick, args=(entry,))
    else:
        render_course_list(entry["courses"])
    action = entry.get("action")  # 평가 기능 이전의 열린 세션 기록에는 요청 종류가 없다
    shortcut = SHORTCUTS.get(action)
    if shortcut and (action == "gap" or entry["courses"] or entry["alternatives"]):
        view, label = shortcut
        st.button(label, key=f"go_{index}", on_click=_go, args=(view,), icon=":material/arrow_forward:")
    if entry["turn_id"] and experiments.active():  # 실험이 꺼져 있으면 평가를 받지 않는다(화면이 전과 같다)
        st.feedback("thumbs", key=f"fb_{entry['turn_id']}", on_change=_on_rating, args=(entry,))


def render(job: str, sidebar_skills: list[str], session: chat_profiles.ProfileSession | None) -> None:
    from backend import chat

    st.title("학습 상담")
    st.caption("직접 맡은 일이나 사용한 기술을 알려주세요. 부족한 기술이나 교육과정은 원할 때 물어보세요.")
    if "chat_conversation" not in st.session_state:
        st.session_state.chat_conversation = chat.Conversation()
        st.session_state.chat_history = []
    conversation = st.session_state.chat_conversation
    if not hasattr(conversation, "usage"):
        conversation.usage = SessionUsage()
    history = st.session_state.chat_history
    message = st.chat_input("경험을 적거나 궁금한 점을 물어보세요.", key="chat_message")
    if message and message.strip():
        try:
            reply = chat.handle_message(
                conversation, message,
                job=job, sidebar_skills=sidebar_skills, session=session,
            )
            # 실험용 항목은 없을 수도 있다(없으면 평가 위젯을 붙이지 않는다)
            entry = {"answer": reply.text, "courses": reply.courses, "turn_id": getattr(reply, "turn_id", ""),
                     "model": getattr(reply, "model", ""), "action": getattr(reply, "action", ""),
                     "alternatives": getattr(reply, "alternatives", [])}
        except Exception:
            # 예외에 닉네임, 프로필 또는 원문이 포함될 수 있다.
            entry = {"answer": "메시지 처리 결과를 확인할 수 없습니다.", "courses": [], "turn_id": "", "model": "",
                     "action": "", "alternatives": []}
        history.append({"prompt": message, "job": job, "session_id": conversation.usage.session_id, **entry})

    usage = conversation.usage
    st.metric("이 대화의 누적 토큰", f"{usage.total_tokens:,}",
              help="기록을 시작한 뒤 모델이 보고한 사용량입니다. 재시도를 포함하며 모의 호출은 0토큰입니다.")
    st.caption(f"입력 {usage.input_tokens:,} · 출력 {usage.output_tokens:,} · 세션 ID `{usage.session_id}`")

    for index, entry in enumerate(history):
        if isinstance(entry, tuple):  # 평가 기능 이전의 열린 세션에는 턴 ID가 없다.
            prompt, answer, courses = entry
            entry = {"prompt": prompt, "answer": answer, "courses": courses, "alternatives": [], "turn_id": ""}
        with st.chat_message("user"):
            st.text(entry["prompt"])
        with st.chat_message("assistant"):
            _render_answer(entry, index)
