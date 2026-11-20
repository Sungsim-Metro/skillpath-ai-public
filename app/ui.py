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


# 화면 전체에 한 번 넣는 스타일(2026-11-20). 고정 문자열이라 사용자 입력이 들어가지 않는다.
# 선택자는 위젯 key 가 만드는 st-key-* 클래스와 Streamlit 의 data-testid 만 쓴다. 색은 .streamlit/config.toml 과 같다.
CSS = """<style>
/* 본문: 너무 넓게 퍼지지 않게 폭을 잡고 제목 크기를 낮춘다 */
[data-testid="stMainBlockContainer"], [data-testid="stBottomBlockContainer"] { max-width: 1240px; }
[data-testid="stMainBlockContainer"] { padding-top: 3.5rem; }
[data-testid="stMainBlockContainer"] h1 { font-size: 2rem; padding-bottom: 0.25rem; }
[data-testid="stMainBlockContainer"] h3 { font-size: 1.3rem; }

/* 사이드바 메뉴: 라디오를 목록형 메뉴로 보이게 한다 */
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stWidgetLabel"] { display: none; }
[data-testid="stSidebar"] [data-testid="stRadio"], [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] { width: 100%; gap: 2px; }
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > div { width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"] { width: 100%; margin: 0; padding: 0.5rem 0.75rem; border-radius: 0.5rem; cursor: pointer; }
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"] > div:not(:last-child) { display: none; }
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"]:hover { background: #E7ECF3; }
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"]:has(input:checked) { background: #D6E2F6; }
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stRadioOption"]:has(input:checked) p { font-weight: 700; color: #1F5FBF; }

/* 숫자 카드와 '먼저 채울 기술' 카드 */
[data-testid="stMetric"] { background: #FFFFFF; box-shadow: 0 1px 2px rgba(26, 31, 43, 0.06); }
[data-testid="stMetricValue"] { font-size: 1.9rem; font-weight: 700; }
[class*="st-key-priority_"] { border-top: 3px solid #1F5FBF; box-shadow: 0 1px 3px rgba(26, 31, 43, 0.08); }

/* 단계 표시, 다음 단계 버튼, 설문 안내 상자 */
.st-key-journey p { background: #F3F5F8; display: inline-block; padding: 0.3rem 0.8rem; border-radius: 999px; }
.st-key-journey strong { color: #1F5FBF; }
[class*="st-key-next_"] { margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #E3E7ED; }
[class*="st-key-step_"] button { padding: 0.6rem 1.4rem; }
.st-key-survey_card { background: #F3F5F8; border-left: 4px solid #1F5FBF; margin-top: 1rem; }

/* 상담: 추천 질문은 알약 모양 */
[class*="st-key-suggest_"] button { border-radius: 999px; }
[class*="st-key-suggest_"] button:hover { background: #D6E2F6; border-color: #9DB4DA; color: #1F5FBF; }

/* 좁은 화면(폰): 깨지지 않을 정도만 맞춘다. 열은 Streamlit 이 세로로 쌓는다 */
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding: 3.5rem 1rem 6rem; }
  [data-testid="stMainBlockContainer"] h1 { font-size: 1.6rem; }
  [data-testid="stMetricValue"] { font-size: 1.5rem; }
  .st-key-journey p { border-radius: 0.75rem; }
  /* 숫자 카드는 한 줄에 둘씩 놓아 첫 화면을 다 차지하지 않게 한다 */
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { flex-direction: row !important; flex-wrap: wrap; gap: 0.5rem; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > [data-testid="stColumn"] {
    flex: 1 1 calc(50% - 0.5rem) !important; min-width: calc(50% - 0.5rem) !important; width: auto !important; }
  [class*="st-key-step_"], [class*="st-key-step_"] button { width: 100%; }
  /* 대화 기록 패널은 숨기고 입력창을 화면 폭대로 쓴다(패널 CSS 가 뒤에 들어오므로 !important) */
  [data-testid="stColumn"]:has(.st-key-history_panel) { display: none !important; }
  .st-key-chat_message { margin-left: 0 !important; width: 100% !important; }
}
</style>"""


def inject_css() -> None:
    """공통 스타일을 넣는다. app/main.py 가 화면마다 한 번 부른다."""
    st.markdown(CSS, unsafe_allow_html=True)


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
