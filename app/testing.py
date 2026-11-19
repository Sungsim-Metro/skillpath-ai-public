"""화면 테스트가 함께 쓰는 도우미. 화면을 여는 방법이 바뀌면 여기만 고친다."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

MAIN = str(Path(__file__).with_name("main.py"))
# 사이드바 메뉴에 놓이는 순서(app/main.py 의 VIEWS)
MENU = {"chat": "학습 상담", "demand": "시장 수요", "gap": "부족 역량", "recommend": "학습 추천",
        "connect": "현직자에게 물어보기"}


def open_app(view: str | None = None, **query) -> AppTest:
    """화면을 한 번 실행해 돌려준다. view 를 주지 않으면 첫 화면(학습 상담)이다. query 는 주소 뒤에 붙는 값이다."""
    at = AppTest.from_file(MAIN, default_timeout=60)
    if view:
        at.query_params["view"] = view
    for key, value in query.items():
        at.query_params[key] = value
    return at.run()
