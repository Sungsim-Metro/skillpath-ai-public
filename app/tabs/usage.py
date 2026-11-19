"""토큰·비용 구역: 이 서버가 한 Bedrock 호출 수, 토큰, 추정 비용. 관리자 화면(app/admin.py)의 한 탭이다.

집계는 backend/usage.py 가 코드로 한다. 여기서는 표로 보여주기만 한다. 프롬프트나 응답 내용은 기록에도 화면에도 없다.
"""
import pandas as pd
import streamlit as st

from app.common import EMPTY, Ctx, load, notice
from backend import usage


def render(ctx: Ctx):
    data, err = load(usage.summarize)
    if err:
        notice("운영 현황", err)
        return
    total, currency = data["total"], data["pricing"]["currency"] or ""
    priced = bool(data["priced_models"]) and total["unpriced_calls"] < total["calls"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("이 서버의 Bedrock 호출", f"{total['calls']}번", help=f"그중 재시도 {total['retries']}번", border=True)
    c2.metric("입력 토큰", f"{total['input_tokens']:,}", border=True)
    c3.metric("출력 토큰", f"{total['output_tokens']:,}", border=True)
    partial = " 이상" if total["unpriced_calls"] else ""
    c4.metric("추정 비용", f"{total['cost']:.4f} {currency}{partial}" if priced else EMPTY, border=True,
              help="토큰 수 × data/pricing.json 의 단가. 청구서 금액이 아니라 추정입니다."
                   + (" 단가가 없는 모델의 호출은 빠져 있어 실제는 이보다 큽니다." if partial else ""))
    if not total["calls"]:
        st.info("실제 모드(BEDROCK_MOCK=0) 호출 기록이 아직 없습니다."
                + (f" 가짜 모드 호출은 {data['mock_calls']}번 있었고 토큰은 0으로 셉니다." if data["mock_calls"] else ""))
        return
    if total["unpriced_calls"]:
        st.warning(f"단가가 없는 모델의 호출 {total['unpriced_calls']}번은 비용에 넣지 않았습니다. "
                   f"`data/pricing.json`에 모델 {', '.join(m or '(모델 ID 없음)' for m in data['unpriced_models'])}의 단가를 적으면 계산합니다.")

    def table(rows: list[dict], group_columns: dict[str, str]) -> pd.DataFrame:
        return pd.DataFrame([
            {**{title: r[key] for key, title in group_columns.items()},
             "호출": r["calls"], "재시도": r["retries"], "입력 토큰": r["input_tokens"],
             "출력 토큰": r["output_tokens"], "합계 토큰": r["total_tokens"],
             f"추정 비용({currency})": f"{r['cost']:.4f}" if priced and not r["unpriced_calls"] else EMPTY}
            for r in rows
        ])

    st.subheader("대화 세션별")
    if data["by_session"]:
        columns = {"session_id": "세션 ID", "last": "최근 호출 (한국 시간)"}
        st.dataframe(table(data["by_session"], columns), hide_index=True, width="stretch")
    else:
        st.caption("세션 ID가 기록된 실제 호출이 없습니다.")
    st.subheader("단계별")
    st.dataframe(table(data["by_stage"], {"label": "단계"}), hide_index=True, width="stretch")
    st.subheader("모델별")
    st.dataframe(table(data["by_model"], {"model": "모델"}), hide_index=True, width="stretch")
    st.subheader("날짜별")
    st.dataframe(table(data["by_day"], {"day": "날짜"}), hide_index=True, width="stretch")
    st.caption(
        f"기록 기간 {data['first']} ~ {data['last']}. 이 서버에서 일어난 호출만 셉니다(추출 일괄 처리처럼 다른 곳에서 돌린 호출은 그곳의 기록에 남습니다). "
        f"기록된 임베딩 입력 토큰도 포함합니다. 가짜 모드 호출 {data['mock_calls']}번은 뺐습니다."
        + (f" 단가 출처: {data['pricing']['source']} ({data['pricing']['checked']} 확인)." if data["pricing"]["source"] else "")
    )
