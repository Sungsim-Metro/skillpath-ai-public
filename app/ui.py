"""화면 사이를 잇는 도우미: 단계 표시, 다음 단계 버튼, 설문 안내 (2026-11-20).

사용자가 상담 → 부족 역량 → 학습 추천 → 설문 순서를 화면만 따라가도 지나가게 한다.
화면 코드만 둔다. 계산은 하지 않고 화면 값(`st.session_state.view`)만 바꾼다.
"""
import os

import streamlit as st

# 단계 표시에 놓이는 순서 (화면 값, 이름). 시장 수요는 '부족 역량'의 근거를 보는 화면이라 같은 단계로 본다.
JOURNEY = (("chat", "내 경험 말하기"), ("gap", "부족 역량 확인"), ("recommend", "학습 추천 보기"), ("survey", "설문"))
JOURNEY_STEP = {"demand": "gap", "gap": "gap", "recommend": "recommend"}
NUMBERS = "①②③④"


def survey_url() -> str:
    """사용자 테스트 설문 주소. https 주소가 아니면 빈 값이다(설문 안내를 그리지 않는다)."""
    url = os.getenv("SURVEY_URL", "").strip()
    return url if url.startswith("https://") else ""


def go(view: str, **state) -> None:
    """사이드바 메뉴의 화면을 바꾼다. 메뉴가 이미 그려진 뒤에는 값을 바꿀 수 없어 버튼의 on_click 으로만 부른다.

    state 는 옮겨 간 화면이 읽을 값이다(예: 시장 수요에서 바로 열어 둘 기술).
    """
    st.session_state.view = view
    for key, value in state.items():
        st.session_state[key] = value


def journey(view: str) -> None:
    """제목 아래 한 줄 단계 표시. 지금 화면의 단계만 굵게 적는다. 단계에 들지 않는 화면에는 그리지 않는다."""
    current = JOURNEY_STEP.get(view)
    if not current:
        return
    steps = [step for step in JOURNEY if step[0] != "survey" or survey_url()]
    parts = [f"**{NUMBERS[n]} {name}**" if key == current else f"{NUMBERS[n]} {name}"
             for n, (key, name) in enumerate(steps)]
    with st.container(key="journey"):
        st.caption(" › ".join(parts))


def next_step(view: str, label: str) -> None:
    """화면 맨 아래의 '다음 단계' 버튼. 누르면 그 화면으로 옮긴다. 설명 글 없이 버튼 글자로만 알린다."""
    with st.container(key=f"next_{view}", horizontal_alignment="right"):
        st.button(f"다음 · {label}", key=f"step_{view}", type="primary", on_click=go, args=(view,),
                  icon=":material/arrow_forward:")


def survey_card() -> None:
    """본문의 설문 안내 상자. 설문 주소가 있을 때만 그린다. 설문이 묻는 것을 미리 알려 답하기 쉽게 한다."""
    url = survey_url()
    if not url:
        return
    with st.container(border=True, key="survey_card"):
        text, action = st.columns([3, 1], vertical_alignment="center")
        text.markdown("**무엇이 부족한지, 왜 그런지, 추천이 적절했는지** 느낀 대로 알려 주세요.")
        action.link_button("사용 후기 설문 참여하기", url, icon=":material/rate_review:", type="primary", width="stretch")
