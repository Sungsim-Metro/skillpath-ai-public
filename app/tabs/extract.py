"""탭 4: 공고 직접 분석 (라이브 시연용)."""
import pandas as pd
import streamlit as st

from app.common import EMPTY, SECTION_LABEL, TYPE_LABEL, Ctx, load, md_escape, notice
from backend import api

# 칸 제목([주요업무] 등)을 한 줄에 단독으로 써야 추출 결과의 칸(section)이 정해진다(contracts ②).
SAMPLE_POSTING = (
    "[주요업무]\n"
    "AWS 기반 서비스 인프라 운영\n"
    "[자격요건]\n"
    "Python을 활용한 개발 경험\n"
    "Linux 서버 운영 및 트러블슈팅 경험\n"
    "[우대사항]\n"
    "Docker 사용 경험 우대\n"
    "쿠버네티스 운영 경험자 우대"
)


def render(ctx: Ctx):
    st.write("채용공고 본문을 붙여 넣으면 요구 기술과 근거 문장을 뽑습니다.")
    text = st.text_area("공고 본문", value=SAMPLE_POSTING, height=220, key="posting_text")
    if st.button("분석하기", type="primary", key="extract_btn") and text.strip():
        result, err = load(api.extract_posting, text)
        if err:
            notice("공고 분석", err)
        st.session_state["extraction"] = result
        st.session_state["extracted_text"] = text
    result = st.session_state.get("extraction")
    if result and st.session_state.get("extracted_text") == text:
        skills = result["skills"]
        if not skills:
            st.warning("알아볼 수 있는 기술을 찾지 못했습니다.")
        else:
            highlighted = md_escape(text)
            for sk in skills:
                ev = md_escape(sk["evidence"])
                highlighted = highlighted.replace(ev, f":blue-background[{ev}]", 1)
            with st.container(border=True):
                st.subheader("근거 표시")
                st.markdown(highlighted.replace("\n", "  \n"))
            st.subheader("추출 결과")
            st.dataframe(
                pd.DataFrame([
                    {
                        "기술": sk["normalized"],
                        "공고 표현": sk["raw"],
                        "구분": TYPE_LABEL.get(sk["type"], sk["type"]),
                        "칸": SECTION_LABEL.get(sk.get("section"), EMPTY),
                        "근거 문장": sk["evidence"],
                        "확신": "높음" if sk["confidence"] == "high" else "불확실",
                    }
                    for sk in skills
                ]),
                hide_index=True,
                width="stretch",
            )
