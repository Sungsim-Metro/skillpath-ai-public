"""마이페이지의 보유 수준 레이더 차트 (2026-11-20).

영역마다 내 단계(진한 면)와 기준 단계(옅은 점선)를 0~5로 겹쳐 그린다. 꼭짓점에 마우스를 올리면 그 영역의 기술 이름과
두 값을 보여 준다. 값은 backend.api.get_level_radar 가 계산하고(수요 집계를 부르지 않는다) 여기서는 좌표로 옮겨 그리기만 한다.
레이더 차트는 Altair 에 기본으로 없어 꼭짓점 좌표를 만들어 선으로 그린다(app/tabs/radar.py 와 같은 방식).
"""
import math

import altair as alt
import pandas as pd
import streamlit as st

from app.common import ACCENT, MARKET_SUPPLY, load
from backend import api

TOP = 5  # 가장 높은 단계
MINE, NEED = "내 단계", "기준 단계"


def _xy(i: int, n: int, r: float) -> tuple[float, float]:
    angle = 2 * math.pi * i / n
    return r * math.sin(angle), r * math.cos(angle)


def render_level_radar(job: str, levels: dict[str, int]) -> None:
    rows, err = load(api.get_level_radar, job, levels)
    if err or not rows:
        st.caption("보유 수준 차트를 그리지 못했습니다.")
        return
    st.caption(" · ".join(f"{r['area']} {r['level']:.1f}/{r['required']:.1f}" for r in rows) + " (내 단계/기준 단계, 0~5)")
    n = len(rows)
    if n < 3:
        return
    points, grid, spokes, labels = [], [], [], []
    for i, r in enumerate(rows):
        skills = ", ".join(r["skills"])
        for name, value in ((NEED, r["required"]), (MINE, r["level"])):
            x, y = _xy(i, n, value / TOP)
            points.append({"구분": name, "영역": r["area"], "포함 기술": skills, "내 단계": f"{r['level']:.1f}",
                           "기준 단계": f"{r['required']:.1f}", "순서": i, "x": x, "y": y})
        lx, ly = _xy(i, n, 1.18)
        labels.append({"영역": r["area"], "포함 기술": skills, "내 단계": f"{r['level']:.1f}", "기준 단계": f"{r['required']:.1f}",
                       "번호": str(i + 1), "x": lx, "y": ly})
        sx, sy = _xy(i, n, 1.0)
        spokes += [{"축": i, "x": 0.0, "y": 0.0}, {"축": i, "x": sx, "y": sy}]
        for ring in range(1, TOP + 1):
            gx, gy = _xy(i, n, ring / TOP)
            grid.append({"고리": ring, "순서": i, "x": gx, "y": gy})

    half = "min(width, height) / 2"
    x = alt.X("x:Q", axis=None, scale=alt.Scale(domain=[-1.35, 1.35], range=[
        alt.ExprRef(f"width / 2 - {half}"), alt.ExprRef(f"width / 2 + {half}")]))
    y = alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[-1.35, 1.35], range=[
        alt.ExprRef(f"height / 2 + {half}"), alt.ExprRef(f"height / 2 - {half}")]))
    closed = {"interpolate": "linear-closed"}
    tip = ["영역", "포함 기술", "내 단계", "기준 단계"]
    df = pd.DataFrame(points)
    mine, need = df[df["구분"] == MINE], df[df["구분"] == NEED]
    layers = [
        alt.Chart(pd.DataFrame(grid)).mark_line(color="#E1E5EA", strokeWidth=1, **closed).encode(
            x=x, y=y, detail="고리:N", order="순서:Q"),
        alt.Chart(pd.DataFrame(spokes)).mark_line(color="#E1E5EA", strokeWidth=1).encode(x=x, y=y, detail="축:N"),
        # 기준 단계는 옅게(딤), 내 단계는 진하게
        alt.Chart(need).mark_line(filled=True, fill=MARKET_SUPPLY, fillOpacity=0.10, color=MARKET_SUPPLY, strokeWidth=1.5,
                                  strokeDash=[4, 3], **closed).encode(x=x, y=y, order="순서:Q"),
        alt.Chart(mine).mark_line(filled=True, fill=ACCENT, fillOpacity=0.28, color=ACCENT, strokeWidth=2,
                                  **closed).encode(x=x, y=y, order="순서:Q"),
        alt.Chart(need).mark_point(filled=True, size=28, color=MARKET_SUPPLY, opacity=0.7).encode(x=x, y=y, tooltip=tip),
        alt.Chart(mine).mark_point(filled=True, size=55, color=ACCENT, opacity=1).encode(x=x, y=y, tooltip=tip),
        # 축 끝의 번호: 좁은 사이드바에서 이름이 잘리지 않게 번호로 적고, 올리면 영역 이름과 기술을 보여 준다
        alt.Chart(pd.DataFrame(labels)).mark_point(filled=True, size=420, color="#EEF2F7", opacity=1).encode(
            x=x, y=y, tooltip=tip),
        alt.Chart(pd.DataFrame(labels)).mark_text(fontSize=11, fontWeight="bold", color="#3B4350").encode(
            x=x, y=y, text="번호:N", tooltip=tip),
    ]
    st.altair_chart(alt.layer(*layers).properties(height=250).configure_view(stroke=None), width="stretch")
    st.caption("번호: " + " · ".join(f"{i} {r['area']}" for i, r in enumerate(rows, start=1))
               + ". 진한 면이 내 단계, 옅은 점선이 기준 단계입니다. 점이나 번호에 마우스를 올리면 그 영역의 기술이 보입니다. "
               "기준 단계가 아직 정해지지 않은 기술은 3단계(팀 결정값)로 봅니다.")
