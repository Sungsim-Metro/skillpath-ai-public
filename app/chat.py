"""학습 상담 화면과 세션 안에서만 유지하는 대화 표시 기록."""
from functools import lru_cache
from urllib.parse import urlsplit

import streamlit as st

from ai.usage import SessionUsage
from pipeline.aliases import skill_aliases


@lru_cache(maxsize=1)
def skill_options() -> tuple[str, ...]:
    """채팅 입장 시 수요 조회 없이 고를 수 있는 기술 대표 이름."""
    return tuple(skill_aliases())


def nickname_input() -> str:
    """첫 닉네임은 작성 중인 상담을 이어받고, 기존 닉네임이 바뀌면 상담을 비운다."""
    previous = st.session_state.get("chat_identity", "")
    if "chat_nickname" not in st.session_state:
        st.session_state.chat_nickname = previous
    nickname = st.text_input(
        "닉네임 (선택)", key="chat_nickname",
        help="같은 닉네임은 같은 프로필을 사용합니다. 사용 중인 닉네임을 바꾸거나 지우면 새 상담을 시작합니다.",
    ).strip()
    if previous and previous != nickname:
        for key in ("chat_conversation", "chat_history", "sidebar_skills", "dashboard_examples"):
            st.session_state.pop(key, None)
    st.session_state.chat_identity = nickname
    return nickname


def _render_course(course: dict) -> None:
    with st.container(border=True):
        st.text(course["title"])
        details = [course["gap_skill"]] if course.get("gap_skill") else []
        details.append("매칭 불확실" if course["match"] == "uncertain" else "매칭 확실")
        if course.get("level"):
            details.append(f"난이도 {course['level']}")
        if course.get("prerequisites"):
            details.append(f"선수지식 {course['prerequisites']}")
        st.text(" · ".join(details))
        if course.get("reason"):
            st.text(course["reason"])
        url = course.get("url") or ""
        try:
            target = urlsplit(url)
            if target.scheme == "https" and target.hostname:
                st.link_button("교육과정 열기", url)
        except ValueError:
            pass


def render(job: str, sidebar_skills: list[str], nickname: str) -> None:
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
                job=job, sidebar_skills=sidebar_skills, nickname=nickname,
            )
            response = (reply.text, reply.courses)
        except Exception:
            # 예외에 닉네임, 프로필 또는 원문이 포함될 수 있다.
            response = ("메시지 처리 결과를 확인할 수 없습니다.", [])
        history.append((message, *response))

    usage = conversation.usage
    st.metric("이 대화의 누적 토큰", f"{usage.total_tokens:,}",
              help="기록을 시작한 뒤 모델이 보고한 사용량입니다. 재시도를 포함하며 모의 호출은 0토큰입니다.")
    st.caption(f"입력 {usage.input_tokens:,} · 출력 {usage.output_tokens:,} · 세션 ID `{usage.session_id}`")

    for prompt, answer, courses in history:
        with st.chat_message("user"):
            st.text(prompt)
        with st.chat_message("assistant"):
            st.text(answer)
            for course in courses:
                _render_course(course)
