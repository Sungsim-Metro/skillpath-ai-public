"""처음 들어온 사용자에게 기능을 단계별로 보여 주는 안내 창 (2026-11-20).

로그인·회원가입 화면을 지나 처음 들어왔을 때 한 번 뜬다. "다시 보지 않기"는 처음부터 체크돼 있고, 체크를 풀고 닫으면
이번 접속 동안 사이드바의 "사용 안내"로 다시 열 수 있다는 것만 달라진다. 본 기록은 이 브라우저 세션에만 둔다(프로필에 저장하지 않는다).
"""
import streamlit as st

STEPS = (
    (":material/forum:", "학습 상담에서 시작하세요",
     "해 본 일이나 써 본 기술을 편하게 적으면 됩니다. 처음에는 준비하는 직무를 버튼으로 고르고, 아래의 추천 질문을 눌러도 됩니다."),
    (":material/checklist:", "수준 진단 설문",
     "상담 화면의 '수준 진단 설문'에서 세부 능력마다 지금 할 수 있는 일을 고릅니다. 일부만 답해도 저장되고, "
     "대화에서 기술을 말하면 관련 문항을 상담이 먼저 물어보기도 합니다."),
    (":material/insights:", "부족한 역량 보기",
     "상담에서 '뭐가 부족해?'라고 묻거나 메뉴의 '부족 역량'을 열면 채용공고가 요구하는 기술과 내 수준의 차이를 봅니다. "
     "마이페이지의 레이더 차트는 영역별 내 단계와 기준 단계를 보여 줍니다."),
    (":material/school:", "추천 교육과정",
     "'학습 추천'에서 부족한 기술마다 AWS 교육과정 카드를 봅니다. 순위, 평점, 추천 이유가 있고 "
     "'유사 과정'은 그 기술을 직접 다루는지 확인하지 못한 관련 과정입니다. 마음에 드는 과정은 '담기'로 모아 메뉴의 '담은 교육과정'에서 봅니다."),
    (":material/menu:", "왼쪽 메뉴",
     "시장 수요, 현직자에게 물어보기, 진단 보고서는 왼쪽 메뉴에서 엽니다. 로그인하면 진단 결과와 상담에서 말한 경험이 저장됩니다."),
)


def _move(step: int) -> None:
    st.session_state.tour_step = st.session_state.get("tour_step", 0) + step


def _close() -> None:
    st.session_state.tour_open = False
    st.session_state.tour_hidden = bool(st.session_state.get("tour_hide", True))
    st.session_state.pop("tour_step", None)


def reopen() -> None:
    st.session_state.tour_open = True
    st.session_state.tour_step = 0


@st.dialog("처음 오셨나요? 이렇게 쓰세요", on_dismiss=_close)  # X 나 Esc 로 닫아도 버튼으로 닫은 것과 같게
def _dialog() -> None:
    step = min(max(st.session_state.get("tour_step", 0), 0), len(STEPS) - 1)
    icon, title, body = STEPS[step]
    st.markdown(" ".join(f":blue-badge[**{n + 1}**]" if n == step else f":gray-badge[{n + 1}]" for n in range(len(STEPS))))
    st.markdown(f"#### {icon} {title}")
    st.write(body)
    st.checkbox("다시 보지 않기", value=True, key="tour_hide")
    back, forward = st.columns(2)
    back.button("이전", key="tour_prev", disabled=step == 0, on_click=_move, args=(-1,), width="stretch")
    if step < len(STEPS) - 1:
        forward.button("다음", key="tour_next", type="primary", on_click=_move, args=(1,), width="stretch")
    else:
        forward.button("시작하기", key="tour_done", type="primary", on_click=_close, width="stretch")
    st.button("건너뛰기", key="tour_skip", on_click=_close, type="tertiary")


def show_if_open() -> None:
    """안내 창이 열려 있는 동안 매번 다시 그린다(창 안의 버튼을 눌러도 닫히지 않게)."""
    if st.session_state.get("tour_open"):
        _dialog()


def sidebar_link() -> None:
    """'다시 보지 않기'를 풀고 닫은 사람에게만 다시 여는 버튼을 보여 준다."""
    if st.session_state.get("tour_hidden") is False:
        st.button("사용 안내 다시 보기", key="tour_reopen", on_click=reopen, icon=":material/help:", width="stretch")
