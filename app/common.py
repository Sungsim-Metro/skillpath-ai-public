"""화면 여러 곳에서 함께 쓰는 값과 도우미.

탭 파일(`app/tabs/`)은 여기서 상수와 도우미를 가져오고, 사이드바에서 정해진 값은 `Ctx` 로 받는다.
"""
import logging
import re
from dataclasses import dataclass, field

import streamlit as st

UNCERTAIN = ":orange-badge[불확실]"
PENDING = ":orange-badge[개발 중]"
EMPTY = "-"  # 값을 아직 못 받은 칸에 넣는 표시
TYPE_LABEL = {"required": "필수", "preferred": "우대"}
SECTION_LABEL = {"qualification": "자격요건", "duty": "주요업무", "preference": "우대사항", "other": "기타"}
COMMON_AREA = "공통"  # data/skill_areas.json 의 COMMON 영역 이름

# 차트 색. 파랑은 "내 것", 주황은 "시장이 원하는 것"으로만 쓴다.
ACCENT = "#1F5FBF"
ACCENT_LIGHT = "#D6E2F6"
ACCENT_LINE = "#9DB4DA"
MARKET_DEMAND = "#C2570C"
MARKET_SUPPLY = "#7C8796"
MARKET_ROWS = 16  # 시장 Gap 차트에 보여줄 최대 기술 수. 차트별로 수요 비중과 Gap이 큰 순서로 고른다.


logger = logging.getLogger("skillpath.app")


@dataclass
class Ctx:
    """사이드바 입력과 그 입력으로 미리 불러온 값. `app/main.py` 가 만들어 탭마다 넘긴다."""

    job: str
    demand: dict | None  # backend.api.get_demand 결과. 못 받으면 None
    demand_err: str | None
    owned: list[str]  # 내 단계가 기준 단계 이상인 기술 이름(backend.api.owned_skill_names)
    levels: dict[str, int]  # 보유 수준 진단 {세부 능력 id: 0~5} (app/levels.py)
    skill_options: list[str]  # 고를 수 있는 기술 이름
    gap_rows: list[dict] | None  # backend.api.get_gap 결과. 못 받으면 None
    gap_err: str | None
    missing: list[dict] = field(default_factory=list)  # gap_rows 중 보유하지 않은 기술


def md_escape(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#+\-!|~:$])", r"\\\1", text)


class Pending(str):
    """아직 구현 전이라 값을 못 받은 구역의 안내 문구. 오류와 구분해 '개발 중'으로 보여준다."""


def load(fn, *args, **kwargs):
    """backend 함수를 부르고 (결과, 안내 문구)를 돌려준다.

    함수가 하나씩 실제 구현으로 바뀌므로, 한 함수가 아직 없거나 실패해도
    화면 전체가 멈추지 않고 그 구역에만 안내를 띄운다. 구현 전이면 안내 문구가 Pending 이다.
    오류 내용에는 계정 ID 같은 값이 들어 있을 수 있어 화면에는 오류 종류만 보이고 전체는 터미널 로그에 남긴다.
    """
    try:
        return fn(*args, **kwargs), None
    except NotImplementedError as e:
        return None, Pending(str(e))
    except Exception as e:  # noqa: BLE001 - 시연 중 한 구역 오류로 화면 전체가 멈추지 않게
        logger.exception("%s 호출 실패", fn.__name__)
        return None, f"불러오지 못했습니다({type(e).__name__}). 실행한 터미널의 로그를 확인하세요."


def notice(title: str, err: str, will_show: str = ""):
    """값을 못 받은 구역에 띄우는 안내. 구현 전이면 '개발 중' 칸을 비워 두고, 오류면 오류 종류를 보여준다."""
    if isinstance(err, Pending):
        with st.container(border=True):
            st.markdown(f"**{title}** {PENDING}")
            st.caption(" ".join(x for x in (will_show, str(err)) if x))
    else:
        st.info(f"{title}: {err}")


def empty_metrics(labels: list[str]):
    """숫자 카드 자리를 값 없이 보여준다."""
    for col, label in zip(st.columns(len(labels)), labels):
        col.metric(label, EMPTY, border=True)
