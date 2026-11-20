"""현직자 연결 카드: 부족 영역에 맞는 개발자 모임과 직무별 사람 찾기 검색 링크 (2026-11-17 결정).

사이드바 메뉴의 '현직자에게 물어보기' 화면이다(2026-11-20 에 부족 역량 화면에서 떼어 냈다). 사람을 조회하거나 추천하지 않는다. 모임은 공개 페이지를 보고 적은 목록에서 코드가 고르고,
사람 찾기는 검색 주소만 만들어 사용자가 직접 누른다. LLM 을 부르지 않는다.
"""
import streamlit as st

from app.common import Ctx, load, md_escape, notice
from backend import api, communities
from backend.jobs import JOBS


def _community_line(c: dict, areas: list[str] | None = None) -> str:
    why = f" · 부족 영역: {', '.join(areas)}" if areas else ""
    note = f" · {c['note']}" if c.get("note") else ""
    return f"- **{md_escape(c['name'])}** · {c['place']}{why}  \n  :gray[확인한 일정·활동: {c['last_seen']}{md_escape(note)}]"


def render_connect(ctx: Ctx):
    job_title = JOBS[ctx.job]
    st.markdown("**① 부족한 영역을 다루는 개발자 모임**")
    rows, err = load(api.get_area_gap, ctx.job, ctx.levels)
    found = None
    if not err:
        found, err = load(communities.suggest, rows)
    if err:
        notice("개발자 모임", err)
    else:
        meta = found["meta"]
        others = [*found["general"], *found["region"]]
        if found["topic"]:
            st.markdown("\n".join(_community_line(t["community"], t["areas"]) for t in found["topic"]))
            first = found["topic"][0]["community"]
            with st.expander("처음이거나 서울이 아니면 (입문·대학생·지역 모임)"):
                st.markdown("\n".join(_community_line(c) for c in others))
        else:  # 부족한 영역이 없거나, AWS 처럼 여러 영역에 걸친 기술만 부족할 때
            st.write("부족한 영역에 맞는 주제 모임을 고르지 못해 입문·대학생·지역 모임을 보여 드립니다.")
            st.markdown("\n".join(_community_line(c) for c in others))
            first = found["general"][0]
        st.link_button(f"{first['name']} 안내 보기", first["link"], icon=":material/open_in_new:")
        extra = ""
        if found["skipped"]:
            extra += f" 그 밖에 맞는 모임이 있는 영역: {', '.join(found['skipped'])}."
        if found["unmatched"]:
            extra += f" 맞는 모임을 확인하지 못한 영역: {', '.join(found['unmatched'])}."
        st.caption(f"AWSKRUG 일정은 대부분 서울 오프라인이고 참가비와 사전 신청이 있습니다.{extra}")
        with st.expander("이 목록을 어떻게 만들었나"):
            st.caption(f"목록은 공개 페이지를 보고 고른 것이고 {meta['checked']}에 확인했습니다. {meta['limits']}")
            st.caption(f"AWSKRUG 모임: {meta['join']['text']}")
            st.link_button("AWSKRUG Slack", meta["join"]["slack"], icon=":material/open_in_new:")

    st.markdown("**② LinkedIn에서 직접 찾아보기**")
    st.caption("검색어를 넣어 둔 주소만 만들어 드립니다. 이 서비스는 사람 정보를 가져오거나 저장하지 않습니다.")
    job_link, *skill_links = communities.people_search_links(job_title, [r["skill"] for r in ctx.missing])
    st.link_button(job_link["label"], job_link["url"], icon=":material/search:", type="primary")
    for col, link in zip(st.columns(2), skill_links):
        col.link_button(link["label"], link["url"], icon=":material/search:", width="stretch")


def render(ctx: Ctx):
    if ctx.gap_err:
        notice("현직자에게 물어보기", ctx.gap_err, "부족한 영역에 맞는 개발자 모임과 사람 찾기 검색 링크가 여기에 나옵니다.")
        return
    with st.container(border=True):
        st.subheader("현직자에게 물어보기")
        render_connect(ctx)
