"""학습 상담 화면과 세션 안에서만 유지하는 대화 표시 기록.

답마다 좋아요·별로예요 평가를 받고, 과정 추천이 두 가지 방식으로 왔으면 A/B 로 나란히 보여 주고 선택을 받는다
(비교 실험, backend/experiments.py). 어떤 모델·구조인지는 화면에 보여 주지 않는다. 기록에는 내용을 넣지 않는다.
"""
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


def _render_answer(entry: dict) -> None:
    from backend import experiments

    st.text(entry["answer"])
    if len(entry["alternatives"]) == 2:
        for column, label, side in zip(st.columns(2), ("추천 A", "추천 B"), entry["alternatives"]):
            with column:
                st.markdown(f"**{label}**")
                for course in side["courses"]:
                    _render_course(course)
        st.radio("어느 쪽 추천이 더 도움이 되나요?", ["A", "B", "비슷해요"], index=None, horizontal=True,
                 key=f"ab_{entry['turn_id']}", on_change=_on_pick, args=(entry,))
    else:
        for course in entry["courses"]:
            _render_course(course)
    if entry["turn_id"] and experiments.active():  # 실험이 꺼져 있으면 평가를 받지 않는다(화면이 전과 같다)
        st.feedback("thumbs", key=f"fb_{entry['turn_id']}", on_change=_on_rating, args=(entry,))


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

    for entry in history:
        with st.chat_message("user"):
            st.text(entry["prompt"])
        with st.chat_message("assistant"):
            _render_answer(entry)
