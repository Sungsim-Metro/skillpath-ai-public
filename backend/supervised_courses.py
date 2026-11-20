"""상담에서 명시한 기술에 대해 검토를 통과한 과정만 반환한다."""
from __future__ import annotations

from ai.bedrock import BedrockJSONError
from backend import api, chat
from backend.levels import has_skill_rating, skill_levels
from pipeline.aliases import normalize_skill


def _names(values: list[str]) -> dict[str, str]:
    names = {}
    for raw in values:
        name = normalize_skill(raw) or raw.strip()
        if name:
            names.setdefault(name.casefold(), name)
    return names


def _eligible_targets(job: str, targets: list[str], levels: dict, facts: dict) -> tuple[list[str], dict, list[str]]:
    owned = _names(facts.get("skills", []))
    excluded = _names(facts.get("excluded_skills", []))
    names = _names(targets)
    rated = [name for name in names.values() if has_skill_rating(levels, name, job)]
    ratings = skill_levels(levels, rated, job)
    eligible = []
    notes = []
    for key, name in names.items():
        if key in owned and name not in ratings:
            notes.append(f"{name} · 수준 미입력. 보유 수준을 입력한 뒤 과정을 추천합니다.")
        elif key in excluded and ratings.get(name, 0) > 0:
            notes.append(f"{name} · 추천 보류. 입력한 수준과 이번 상담의 정정 내용이 다릅니다. 보유 수준을 확인해 주세요.")
        else:
            eligible.append(name)
    limit = api.RECOMMEND_TOP_N
    if len(eligible) > limit:
        notes.append(f"한 번에 {limit}개 기술을 조회합니다. 다음 요청에서 조회할 기술 · {', '.join(eligible[limit:])}")
    return eligible[:limit], ratings, notes


def _reviewed_cards(job: str, name: str, ratings: dict, evidence: dict) -> list[dict]:
    results = api.get_recommendations(job, [name], review=True, levels=ratings, review_evidence=evidence)
    if len(results) != 1 or results[0]["gap_skill"] != name:
        raise ValueError("요청 기술과 추천 결과가 다릅니다.")
    if name in evidence:
        return chat._verified_courses(results, [{"skill": name, **evidence[name]}])
    if results[0]["demand"] is not None:
        raise ValueError("집계에 없는 기술에 수요 수치가 있습니다.")
    return chat._verified_course_cards(name, results[0]["courses"])


def recommend_for_targets(job: str, targets: list[str], levels: dict, facts: dict) -> chat.Reply:
    """요청 순서를 유지하고, 미입력·조회 실패·연결 과정 없음은 기술별로 알린다."""
    requested, ratings, notes = _eligible_targets(job, targets, levels, facts)
    if not requested:
        return chat.Reply("\n".join(notes) or "과정을 찾을 기술 이름을 알려 주세요.", action="courses")

    reply = chat.Reply("", action="courses")
    evidence = {}
    try:
        snapshot = api.get_demand(job)
        total = snapshot["total_postings"]
        if total > 0:
            requested_names = _names(requested)
            for row in snapshot["skills"]:
                key = (normalize_skill(row["skill"]) or row["skill"].strip()).casefold()
                if key in requested_names:
                    evidence[requested_names[key]] = api.recommendation_demand(row, total)
    except Exception:
        evidence.clear()
        notes.append("직무의 공고 수요를 불러오지 못했습니다. 과정 연결에는 학습 목표만 사용합니다.")
        reply.failed = True

    lines = []
    for name in requested:
        demand = evidence.get(name)
        detail = (f"공고 {demand['of']}건 중 {demand['postings']}건에서 언급" if demand is not None
                  else "확인된 공고 수요 자료 없음")
        if name in ratings:
            detail += f" · 내 수준 {ratings[name]}단계"
        try:
            cards = _reviewed_cards(job, name, ratings, evidence)
            reply.courses.extend(cards)
            if not cards:
                detail += " · 연결된 교육과정을 찾지 못했습니다."
        except (BedrockJSONError, KeyError, TypeError, ValueError):
            detail += " · 추천 근거를 검증하지 못해 과정을 보류했습니다."
            reply.failed = True
        except Exception:
            detail += " · 교육과정 추천을 불러오지 못했습니다."
            reply.failed = True
        lines.append(f"{name} · {detail}")
    reply.text = "\n".join([*lines, *notes])
    return reply
