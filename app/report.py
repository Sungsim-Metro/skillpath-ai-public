"""진단 보고서(마크다운 한 파일) 만들기. 2026-11-16 HTML 로 시작해 11/17 회의에서 `.md` 로 바꿈(#98).

화면에 나온 값만 담는다. 보고서를 위해 LLM 을 다시 부르지 않는다. 수강 순서는 backend/plan.py 가 계산하고,
여기서는 받은 값을 마크다운 글로 옮기기만 하므로 화면 코드로 본다.
데이터에서 온 글자는 마크다운·HTML 로 해석되지 않게 바꿔 넣고(_t), 링크는 http(s) 주소만 건다(_link).
내려받는 파일과 화면 미리 보기(st.markdown)가 같은 글이다.

절 구성: 한눈에 보기 → 다음에 할 일 → 1. 역량 진단 → 2. AWS 교육과정 추천 → 3. 수강 순서
→ 4. 과정으로 채우기 어려운 기술 → 읽을 때 주의할 점
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

BAR_WIDTH = 10


def _today() -> date:
    """한국 날짜. 서버 시간대가 달라도 사용자가 보는 날짜와 맞춘다."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _t(value) -> str:
    """데이터에서 온 글자를 한 줄 글로. 줄바꿈을 없애고 마크다운·HTML 기호와 $(수식)를 글자로 바꾼다."""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return re.sub(r"([\\`*_\[\]<>|#$~])", r"\\\1", text)


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _bar(x: float) -> str:
    filled = round(min(max(x, 0), 1) * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _safe_url(url) -> str:
    """http(s) 주소만 링크로 건다. 마크다운 링크를 깨는 글자(공백, 괄호, <>)는 퍼센트 인코딩한다."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return ""
    return quote(url, safe=":/?&=#%~+,;@!$'*.-_")


def _link(title, url) -> str:
    safe = _safe_url(url)
    return f"[{_t(title)}]({safe})" if safe else _t(title)


def _table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _level(value, names: dict) -> str:
    name = names.get(str(value))
    return f"{value}단계 ({name})" if name else f"{value}단계"


def build_report(*, job_title: str, demand: dict, gap_rows: list[dict], area_rows: list[dict], recommendations: list[dict],
                 plan: list[dict], uncovered: list[dict], market_rows: list[dict] | None = None,
                 level_names: dict | None = None, mock: bool = False, today: date | None = None,
                 recommend_failed: bool = False, min_ratio: float = 0.2) -> str:
    """진단 보고서 마크다운 문자열. 인자는 모두 화면과 backend 가 이미 계산한 값이다.

    plan, uncovered: backend.plan.study_order / uncovered_skills 결과.
    market_rows: backend.api.get_market_gap 의 rows. 못 받았으면 None(절을 건너뛴다).
    recommend_failed: 추천을 불러오지 못했을 때 참. "추천할 과정이 없다"와 구분해 적는다.
    """
    today = today or _today()
    names = level_names or {}
    missing = [r for r in gap_rows if not r["owned"]]
    have = [r for r in gap_rows if r["owned"]]
    recommended = {r["gap_skill"] for r in recommendations}
    skipped = [r["skill"] for r in missing if r["skill"] not in recommended]
    origin = ", ".join(demand.get("sources") or []) or "표기 없음"
    total = demand["total_postings"]
    out: list[str] = []
    add = out.append

    add(f"# {_t(job_title)} 역량 진단 보고서\n")
    add(f"만든 날 {today.isoformat()} · skillpath-ai · 채용공고 {total}건 기준\n")
    if mock:
        add("> **가짜 데이터 모드에서 만든 보고서입니다.** 숫자와 과정은 예시입니다.\n")

    # 한눈에 보기
    add("## 한눈에 보기\n")
    add(f"- 공고의 {_pct(min_ratio)} 이상이 요구한 **주요 기술 {len(gap_rows)}개** 중 **{len(have)}개를 보유**했고, "
        f"**{len(missing)}개가 부족**합니다.")
    if missing:
        top = missing[0]
        add(f"- 가장 많이 요구되는 부족 기술은 **{_t(top['skill'])}**입니다(공고 {top['of']}건 중 {top['postings']}건).")
    if plan:
        add(f"- 추천 과정 중 확실한 과정 {len(plan)}개로 수강 순서를 정했습니다. 첫 과정은 **{_t(plan[0]['title'])}**입니다.")
    if uncovered:
        add(f"- AWS 교육과정으로 확실히 채우기 어려운 기술: {', '.join(_t(u['skill']) for u in uncovered)} (4절)")
    add("")

    # 다음에 할 일
    add("## 다음에 할 일\n")
    if plan:
        add(f"- [ ] {_link(plan[0]['title'], plan[0]['url'])} 등록하기")
    if len(plan) > 1:
        add(f"- [ ] 3절의 수강 순서대로 나머지 {len(plan) - 1}개 과정 듣기")
    if missing:
        add("- [ ] 과정을 마치면 skillpath-ai에서 보유 수준을 다시 진단하고 보고서를 새로 받기")
    if uncovered:
        add("- [ ] 4절의 기술은 공식 문서나 개인 프로젝트로 따로 계획하기")
    if not missing:
        add("- [ ] 주요 요구 기술을 모두 보유했습니다. 공고의 우대 기술과 프로젝트 경험을 보강하세요.")
    add("")

    # 1. 역량 진단
    add("## 1. 역량 진단\n")
    add(f"채용공고 {total}건에서 뽑은 기술 {len(demand['skills'])}개를 기준으로 했습니다. "
        f"수집일 {_t(demand.get('collected') or '표기 없음')}, 출처 {_t(origin)}."
        + (f" {_t(demand['note'])}" if demand.get("note") else "") + "\n")
    add("### 영역별 충족률\n")
    shown = [r for r in area_rows if r["demand"] > 0]
    if shown:
        add(_table(["영역", "충족률", "", "보유 / 수요", "부족한 기술"],
                   [[_t(r["area"]), f"`{_bar(r['coverage'])}`", _pct(r["coverage"]), f"{r['owned']} / {r['demand']}",
                     _t(", ".join(r["missing_skills"]) or "없음")] for r in shown]))
        add("\n수요는 그 영역의 기술을 요구한 공고 건수의 합, 보유는 그중 내가 채운 몫입니다. "
            "필수로 요구한 공고는 기준 단계를 넘으면, 우대로 요구한 공고는 내 단계/5만큼 채운 것으로 셉니다.\n")
    else:
        add("영역별 수요가 없습니다.\n")

    add("### 주요 요구 기술과 내 단계 (수요 높은 순)\n")
    if gap_rows:
        add(_table(["기술", "영역", "요구 공고", "그중 필수", "내 단계", "기준 단계", "판정"],
                   [[_t(r["skill"]), _t(r["area"]), f"{r['postings']} / {r['of']} ({_pct(r['ratio'])})",
                     _pct(r["required_ratio"]), _level(r["user_level"], names),
                     f"{r['required_level']}단계" + (" (추정)" if r.get("required_level_confidence") == "uncertain" else ""),
                     "보유" if r["owned"] else "**부족**"] for r in gap_rows]))
        guessed = [r for r in gap_rows if r.get("required_level_confidence") == "uncertain"]
        add("\n내 단계는 스스로 고른 값이며 실력을 검증한 것이 아닙니다. 내 단계가 기준 단계 이상이면 보유로 봅니다."
            + (f" \"(추정)\"인 {len(guessed)}개 기술은 공고로 기준 단계를 정하지 못해 팀이 정한 추정값을 썼으므로 판정은 참고용입니다."
               if guessed else "") + "\n")
    else:
        add(f"공고의 {_pct(min_ratio)} 이상이 요구한 기술이 없습니다.\n")

    # 2. AWS 교육과정 추천
    add("## 2. AWS 교육과정 추천\n")
    if recommend_failed or (missing and not recommendations):
        add("추천을 불러오지 못해 이 절은 비어 있습니다. 화면의 학습 추천 탭을 다시 확인해 주세요.\n")
    elif not recommendations:
        add("부족한 기술이 없어 추천할 과정이 없습니다.\n")
    else:
        for i, rec in enumerate(recommendations, start=1):
            d = rec["demand"]
            mine = f" · 내 단계 {rec['user_level']}" if "user_level" in rec else ""
            add(f"### 2.{i} {_t(rec['gap_skill'])}\n")
            add(f"공고 {d['of']}건 중 {d['postings']}건이 요구 · 그중 필수 {_pct(d['required_ratio'])}{mine}\n")
            if not rec["courses"]:
                add("이 기술을 다루는 교육과정을 찾지 못했습니다.")
            for c in rec["courses"]:
                tag = " · **불확실**" if c.get("match") == "uncertain" else ""
                add(f"- **{_link(c['title'], c.get('url'))}**{tag}  ")
                add(f"  난이도 {_t(c.get('level', ''))} · 선수지식 {_t(c.get('prerequisites', ''))}  ")
                add(f"  추천 이유: {_t(c['reason'])}")
            add("")
        if skipped:
            add(f"수요가 높은 {len(recommendations)}개 기술만 추천했습니다. 이번에 추천하지 않은 기술: {_t(', '.join(skipped))}\n")

    # 3. 수강 순서
    add("## 3. 수강 순서\n")
    if plan:
        add(_table(["순서", "과정", "채우는 기술", "난이도", "먼저 들을 것"],
                   [[p["step"], _link(p["title"], p["url"]), _t(p["skill"]), _t(p["level"]),
                     _t(", ".join(p["after"])) + " 과정" if p["after"] else "-"] for p in plan]))
        add("\n수요가 높은 기술의 과정부터, 같은 기술 안에서는 내 단계에 맞는 난이도부터 놓았습니다. "
            "선수지식에 다른 부족 기술이 적힌 과정은 그 기술의 과정 뒤로 미뤘습니다. "
            "\"불확실\" 과정은 순서에 넣지 않았습니다. 소요 시간은 과정 링크에서 확인하세요.\n")
    else:
        add("확실한 추천 과정이 없어 수강 순서를 만들지 않았습니다.\n")

    # 4. 과정으로 채우기 어려운 기술
    add("## 4. 과정으로 채우기 어려운 기술\n")
    if uncovered:
        label = {"none": "과정을 찾지 못함", "uncertain": "관련 과정만 있음(불확실)"}
        add(_table(["기술", "상태"], [[_t(u["skill"]), label[u["status"]]] for u in uncovered]))
        add("\n그럴듯한 과정으로 채우지 않고 비워 두었습니다. 공식 문서, 실습, 프로젝트로 따로 준비하세요.\n")
    else:
        add("추천한 기술은 모두 확실한 AWS 교육과정이 있습니다.\n")
    missing_names = {r["skill"] for r in missing}
    market = [r for r in (market_rows or []) if r["skill"] in missing_names]
    if market:
        add("### 시장 Gap (내 부족 기술)\n")
        add(_table(["기술", "공고 수요 비중", "교육 공급 비중", "Gap"],
                   [[_t(r["skill"]), f"{r['demand_pct']:.1f}%", f"{r['supply_pct']:.1f}%", f"{r['gap_pp']:+.1f}%p"]
                    for r in market]))
        add("\nGap = 수요 비중 − 공급 비중. +이면 시장은 원하는데 교육이 덜 다루는 기술이라 과정만으로 채우기 어렵습니다.\n")

    add("## 읽을 때 주의할 점\n")
    add("- 무엇을 공부할지 안내하는 자료이며 실력을 검증하거나 합격을 보장하지 않습니다.")
    add("- 수요는 수집한 공고에 한정된 숫자이고, 경력 공고가 섞여 있습니다.")
    add("- \"불확실\"로 표시한 과정은 그 기술을 직접 다룬다는 근거를 찾지 못한 관련 과정입니다. 링크에서 내용을 확인하세요.")
    add("- 이 보고서는 서비스에 저장되지 않습니다.")
    return "\n".join(out) + "\n"


def file_name(today: date | None = None) -> str:
    return f"skillpath-report-{(today or _today()).strftime('%Y%m%d')}.md"
