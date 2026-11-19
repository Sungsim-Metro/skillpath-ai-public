"""관리자 화면 (/admin?admin=<ADMIN_KEY 값>, 2026-11-19 회의).

비교 실험 결과, 토큰·비용, 데이터 현황, 교육과정이 덮지 못하는 기술, 평가 결과를 한 곳에 모은다.
계산은 전부 backend 가 코드로 하고 여기서는 표로 보여 주기만 한다. 대화 내용은 어디에도 없다.
"""
import hmac
import os

import pandas as pd
import streamlit as st

from app.common import EMPTY, load, notice
from app.tabs import usage as tab_usage
from backend import api, eval_results, experiments
from backend.jobs import JOBS

COVERAGE_LABEL = {"certain": "직접 다루는 과정 있음", "uncertain": "관련 과정만(불확실)", "none": "과정 없음",
                  "unknown": "확인하지 못함"}
DECISION_LABEL = {"decided": "결정", "provisional": "참고용(평가 부족)", "insufficient": "비교 불가"}


def allowed(value) -> bool:
    """주소의 admin 값이 ADMIN_KEY 와 같은가. ADMIN_KEY 가 비어 있으면 열리지 않는다."""
    key = os.getenv("ADMIN_KEY", "")
    return bool(key) and hmac.compare_digest(str(value or "").encode(), key.encode())


def _ratio(value) -> str:
    return EMPTY if value is None else f"{value:.0%}"


def _number(value, digits: int = 0) -> str:
    return EMPTY if value is None else f"{value:,.{digits}f}"


def _models() -> None:
    st.subheader("실험 1. 모델 비교")
    st.caption("메시지마다 모델을 무작위로 배정하고 답 아래에서 좋아요·별로예요를 받았습니다. 채팅 본문은 코드가 쓰므로 "
               "평가는 모델이 한 일(발화 해석, 추천 이유)에 대한 것입니다. 좋아요 비율은 평가한 턴 중의 비율입니다.")
    summary, err = load(experiments.summarize_models)
    if err:
        notice("모델 비교", err)
        return
    if not summary:
        st.info("아직 기록된 턴이 없습니다. 실험 설정(EXPERIMENT_MODELS)이 켜져 있고 상담이 한 번 이상 있어야 합니다.")
        return
    currency = summary[0]["currency"] or ""
    st.dataframe(pd.DataFrame([
        {"모델": m["model"], "턴": m["turns"], "평가": m["rated"], "좋아요": m["up"], "별로예요": m["down"],
         "좋아요 비율": _ratio(m["up_ratio"]), "검사 실패": m["failed"], "실패 비율": _ratio(m["failed_ratio"]),
         "턴당 입력 토큰": _number(m["avg_input_tokens"]), "턴당 출력 토큰": _number(m["avg_output_tokens"]),
         f"턴당 추정 비용({currency})": _number(m["avg_cost"], 5)}
        for m in summary
    ]), hide_index=True, width="stretch")
    decision = experiments.decide_model(summary)
    rule = decision["rule"]
    st.markdown(f"**쓸 모델: {decision['model'] or EMPTY}** · {DECISION_LABEL[decision['status']]}")
    st.caption(f"{decision['reason']} 규칙: 좋아요 비율이 가장 높은 모델과 {rule['tolerance'] * 100:.0f}%p 안인 모델 중 "
               f"턴당 추정 비용이 가장 낮은 모델. 모델마다 평가 {rule['min_ratings']}건 이상이어야 결정으로 봅니다. "
               "비용은 토큰 수 × data/pricing.json 단가로 낸 추정입니다. 과정 추천 턴은 A/B 로 구조 두 개를 모두 돌린 토큰이 "
               "들어 있어 실제 한 번의 비용보다 큽니다(두 모델에 똑같이 적용되므로 모델끼리의 비교에는 영향이 없습니다).")
    if st.button("이 결과를 결정 기록으로 남기기", key="save_decision", disabled=decision["status"] == "insufficient"):
        _, save_err = load(experiments.save_decision, summary, decision)
        if save_err:
            notice("결정 기록", save_err)
        else:
            st.success("지금의 집계와 고른 모델을 기록했습니다.")
    records, err = load(experiments.load_decisions)
    if err:
        notice("결정 기록", err)
    elif records:
        st.markdown("**남긴 결정 기록**")
        st.dataframe(pd.DataFrame([
            {"기록 시각(한국 시간)": r["ts"], "고른 모델": r["decision"].get("model") or EMPTY,
             "상태": DECISION_LABEL.get(r["decision"].get("status"), EMPTY), "이유": r["decision"].get("reason", ""),
             "그때의 집계": ", ".join(f"{m['model']} 좋아요 {_ratio(m['up_ratio'])}({m['rated']}건) "
                                 f"비용 {_number(m['avg_cost'], 5)}" for m in r.get("models", []))}
            for r in records
        ]), hide_index=True, width="stretch")
        st.caption("기록만 남깁니다. 실제로 쓰는 모델은 서버 설정(BEDROCK_MODEL_ID)을 바꿔야 바뀝니다.")


def _structures() -> None:
    st.subheader("실험 2. 추천 구조 A/B")
    st.caption("과정 추천을 두 구조로 만들어 A/B 로 보여 주고 선택을 받았습니다. 좌우 순서는 무작위이고 구조 이름은 보여 주지 않았습니다. "
               "single 은 한 번에 쓰기, reviewed 는 쓰기→검토→고치기입니다.")
    summary, err = load(experiments.summarize_structures)
    if err:
        notice("추천 구조 A/B", err)
        return
    if not summary:
        st.info("아직 A/B 선택이 없습니다.")
        return
    st.dataframe(pd.DataFrame([
        {"구조": s["structure"], "선택받은 비교 수": s["shown"], "이 구조를 고름": s["picked"], "비슷해요": s["ties"],
         "선택률": _ratio(s["pick_ratio"]), "추천 한 번 입력 토큰": _number(s["avg_input_tokens"]),
         "추천 한 번 출력 토큰": _number(s["avg_output_tokens"])}
        for s in summary
    ]), hide_index=True, width="stretch")
    if any(s["shown"] < experiments.MIN_RATINGS for s in summary):
        st.caption(f"선택이 {experiments.MIN_RATINGS}건에 못 미쳐 참고용입니다.")


def _data_status() -> None:
    for job, title in JOBS.items():
        st.subheader(title)
        status, err = load(api.get_demand_status, job)
        if err:
            notice(f"{title} 데이터 현황", err, "공고 수, 출처, 추출 품질이 여기에 나옵니다.")
            continue
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("분석한 공고", f"{status['total_postings']}건", border=True)
        c2.metric("수요로 센 기술", f"{status['counted_skills']}개", border=True)
        c3.metric("기술이 안 뽑힌 공고", _number(status["empty_extraction_postings"]), border=True)
        c4.metric("불확실 기술만 있는 공고", _number(status["uncertain_only_postings"]), border=True)
        st.caption(f"출처 {', '.join(status['sources']) or EMPTY} · 수집 {status['collected'] or EMPTY} · "
                   f"집계 시각 {status['generated_at'] or EMPTY}(UTC) · 선정 버전 {status['selection_version'] or EMPTY}")
        if status["uncertain_skills"]:
            st.markdown(f"**표준 기술 사전에 없어 수요로 세지 못한 기술 {status['uncertain_skill_count']}개 중 상위 20개**")
            st.dataframe(pd.DataFrame([{"기술(공고의 표기)": r["skill"], "언급한 공고": r["postings"]}
                                       for r in status["uncertain_skills"][:20]]), hide_index=True, width="stretch")
        if status["excluded_postings"]:
            with st.expander(f"집계에서 뺀 공고 {len(status['excluded_postings'])}건"):
                st.dataframe(pd.DataFrame([{"공고 ID": r["posting_id"], "사유": r["reason"]}
                                           for r in status["excluded_postings"]]), hide_index=True, width="stretch")


def _coverage() -> None:
    st.caption("수요에 잡힌 기술마다 AWS 교육과정이 있는지 규칙(과정 제목·학습 목표, 세부 능력 연결)으로 본 것입니다. "
               "모델과 검색 서비스를 부르지 않습니다. 실제 추천은 관련 과정을 더 찾아 붙이므로 '과정 없음'도 화면에서는 "
               "불확실한 과정이 함께 나올 수 있습니다.")
    for job, title in JOBS.items():
        st.subheader(title)
        rows, err = load(api.get_course_coverage, job)
        if err:
            notice(f"{title} 과정 커버리지", err)
            continue
        counts = {status: sum(r["status"] == status for r in rows) for status in COVERAGE_LABEL}
        for column, (status, label) in zip(st.columns(len(COVERAGE_LABEL)), COVERAGE_LABEL.items()):
            column.metric(label, f"{counts[status]}개", border=True)
        st.dataframe(pd.DataFrame([
            {"기술": r["skill"], "요구 공고": r["postings"], "요구 비율": _ratio(r["ratio"]),
             "교육과정": COVERAGE_LABEL[r["status"]], "찾은 과정 수": _number(r["courses"])}
            for r in sorted(rows, key=lambda r: (list(COVERAGE_LABEL).index(r["status"]), -r["postings"]), reverse=False)
        ]), hide_index=True, width="stretch")


def _evaluation() -> None:
    results, err = load(eval_results.list_results)
    st.subheader("평가 실행 결과")
    if err:
        notice("평가 실행 결과", err)
    elif not results:
        st.info("이 서버에 평가 결과 파일이 없습니다. 평가 스크립트(ai/eval/)가 만든 outputs/eval/*.json 을 올리면 여기에 나옵니다.")
    else:
        for item in results:
            cases = f" · 문항 {item['cases']}개" if item["cases"] is not None else ""
            st.markdown(f"**{item['file']}** · {item['modified']}{cases}")
            if item["rows"]:
                st.dataframe(pd.DataFrame(item["rows"]), hide_index=True, width="stretch")
            else:
                st.caption("표로 보여 줄 요약이 없는 파일입니다." if "error" not in item else "파일을 읽지 못했습니다.")
    st.subheader("개선 기록의 발표용 요약")
    table, err = load(eval_results.summary_table)
    if err or not table:
        st.caption("요약 표를 찾지 못했습니다(ai/eval/IMPROVEMENT_LOG.md).")
    else:
        st.markdown(table)
        st.caption("사람이 적은 기록을 그대로 옮긴 것입니다. 조건과 주의할 점은 ai/eval/IMPROVEMENT_LOG.md 에 있습니다.")


def render() -> None:
    st.title("skillpath-ai 관리자")
    mode = "가짜 데이터 모드(BEDROCK_MOCK=1)" if api.is_mock() else "실제 모드(BEDROCK_MOCK=0)"
    st.caption(f"{mode}. 대화 내용은 저장하지 않으며 이 화면에도 없습니다.")
    tabs = st.tabs(["비교 실험", "토큰·비용", "데이터 현황", "과정 커버리지", "평가 결과"])
    with tabs[0]:
        _models()
        _structures()
    with tabs[1]:
        tab_usage.render(None)
    with tabs[2]:
        _data_status()
    with tabs[3]:
        _coverage()
    with tabs[4]:
        _evaluation()
