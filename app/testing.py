"""화면 테스트가 함께 쓰는 도우미. 화면을 여는 방법이 바뀌면 여기만 고친다."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

MAIN = str(Path(__file__).with_name("main.py"))
# 사이드바 메뉴에 놓이는 순서(app/main.py 의 VIEWS)
MENU = {"chat": "학습 상담", "demand": "시장 수요", "gap": "부족 역량", "recommend": "학습 추천",
        "connect": "현직자에게 물어보기", "report": "진단 보고서"}


def set_job(at: AppTest, job: str) -> AppTest:
    """목표 직무를 바꾸고 다시 그린다. 화면에서는 학습 상담의 직무 버튼이나 대화로 바꾼다."""
    at.session_state["job"] = job
    return at.run()


def open_app(view: str | None = None, *, entered: bool = True, **query) -> AppTest:
    """화면을 한 번 실행해 돌려준다. view 를 주지 않으면 첫 화면(학습 상담)이다. query 는 주소 뒤에 붙는 값이다.

    entered 가 True 면 로그인·회원가입 화면을 비회원으로 지난 상태로 연다. 그 화면은 app/test_entry.py 가 본다.
    """
    at = AppTest.from_file(MAIN, default_timeout=60)
    if entered:
        at.session_state["entered"] = True
    if view:
        at.query_params["view"] = view
    for key, value in query.items():
        at.query_params[key] = value
    return at.run()
