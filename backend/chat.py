"""학습 상담 상태를 합치고, 사용자가 요청한 범위의 도구만 실행한다."""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace
from itertools import groupby
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from ai import chat as intake
from ai import chat_reply
from ai.bedrock import BedrockJSONError, current_model, use_model
from ai.usage import SessionUsage, current_session, track_session
from backend import api, chat_profiles, experiments, lambda_tools
from backend.jobs import JOBS
from backend.levels import has_skill_rating
from pipeline.aliases import normalize_skill
from pipeline.search import COURSE_LEVELS

if TYPE_CHECKING:
    from ai.supervisor import SupervisedTurn


@dataclass
class Conversation:
    """접속 중인 한 이용자의 대화 상태. False인 수정값은 이전 보유 진술을 취소한다."""

    session: chat_profiles.ProfileSession | None = field(default=None, repr=False)
    profile: dict | None = None
    job: str | None = None
    needs_role: bool = False
    pending_save: bool = False
    offered_gap: bool = False
    skills: dict[str, bool] = field(default_factory=dict)
    experiences: dict[str, bool] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    last_changes: dict = field(default_factory=dict)
    usage: SessionUsage = field(default_factory=SessionUsage)
    wishes: list[str] = field(default_factory=list)
    pending_course_question: bool = False
    last_gap_skills: list[str] = field(default_factory=list)
    last_gap_job: str | None = None
    last_course_job: str | None = None
    last_course_levels: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Reply:
    """한 턴의 답. turn_id 아래는 비교 실험(backend/experiments.py) 기록용이다.

    alternatives 가 둘이면 화면이 A/B 로 나란히 보여 준다: [{"structure", "courses", "tokens"}] 왼쪽, 오른쪽 순서.
    failed 는 모델 응답이 검사를 통과하지 못해 답을 만들지 못한 턴이다.
    """

    text: str
    courses: list[dict] = field(default_factory=list)
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    model: str = ""
    action: str = ""
    alternatives: list[dict] = field(default_factory=list)
    failed: bool = False
    assessment_skills: list[str] = field(default_factory=list)
    assessment_job: str = ""
    supervisor_decisions: list[str] = field(default_factory=list)
    gap_skills: list[str] = field(default_factory=list)
    course_targets: list[str] = field(default_factory=list)


def _skill(name: str) -> str:
    return normalize_skill(name) or name.strip()


def _effective(base: list[str], edits: dict[str, bool]) -> list[str]:
    merged = dict.fromkeys(base, True)
    merged.update(edits)
    return [name for name, present in merged.items() if present]


def _context(state: Conversation, sidebar_job: str, base_skills: list[str]) -> dict:
    profile = state.profile or {}
    job = state.job or profile.get("job") or sidebar_job
    course_levels = (getattr(state, "last_course_levels", {})
                     if job == getattr(state, "last_course_job", None) and not state.needs_role else {})
    return {
        "job": job if job in JOBS and not state.needs_role else None,
        "needs_role": state.needs_role,
        "pending_save": state.pending_save,
        "skills": _effective(base_skills, state.skills),
        "excluded_skills": [skill for skill, present in state.skills.items() if not present],
        "experiences": _effective(profile.get("experiences", []), state.experiences),
        "history": state.history,
        "last_changes": state.last_changes,
        "last_course_skills": list(course_levels),
        "last_course_levels": course_levels,
    }


def _apply_turn(state: Conversation, turn: intake.Turn) -> None:
    added = turn.add_skills
    removed = turn.remove_skills
    state.skills.update(dict.fromkeys(added, True))
    state.skills.update(dict.fromkeys(removed, False))
    state.experiences.update(dict.fromkeys(turn.add_experiences, True))
    state.experiences.update(dict.fromkeys(turn.remove_experiences, False))
    if turn.job:
        state.job = turn.job
        state.needs_role = False
    if added or removed or turn.add_experiences or turn.remove_experiences or turn.job:
        state.last_changes = {
            "add_skills": added, "remove_skills": removed,
            "add_experiences": turn.add_experiences, "remove_experiences": turn.remove_experiences,
            "job": turn.job,
        }


def _save(state: Conversation, turn: intake.Turn, facts: dict) -> str:
    """확인된 항목 중 사용자가 지정한 내용만 원자적으로 저장한다."""
    changes = {}
    labels = []
    if turn.save_job and facts["job"] in JOBS:
        changes["job"] = facts["job"]
        labels.append(JOBS[facts["job"]])
    for key, selected, edits in (
        ("skills", turn.save_skills, state.skills),
        ("experiences", turn.save_experiences, state.experiences),
    ):
        for item in dict.fromkeys(selected):
            if item in facts[key]:
                op = "add"
            elif edits.get(item) is False:
                op = "remove"
            else:
                state.pending_save = True
                return "프로필에 반영할 기술이나 직접 맡은 일을 구체적으로 알려 주세요."
            stored_names = []
            if key == "skills":
                # 예전 프로필의 별칭도 실제 저장 표기로 수정한다.
                stored_names = [s for s in (state.profile or {}).get("skills", [])
                                if _skill(s).casefold() == item.casefold()]
            values = stored_names if op == "remove" else stored_names[:1]
            changes.setdefault(f"{op}_{key}", []).extend(values or [item])
            labels.append(item if op == "add" else f"{item} (삭제)")
    if not changes:
        state.pending_save = True
        return "프로필에 저장할 항목을 지정해 주세요."
    if state.session is None:
        return "프로필을 저장하려면 사이드바에서 로그인한 뒤 저장할 항목을 다시 말씀해 주세요."
    try:
        state.profile = chat_profiles.update_profile(state.session, **changes)
    except Exception:
        return "프로필 저장에 실패했습니다. 이번 상담 내용은 유지됩니다."
    return "프로필에 저장했습니다. " + ", ".join(labels)


def _summary(facts: dict) -> str:
    role = JOBS.get(facts["job"], "미정")
    skills = ", ".join(facts["skills"]) or "아직 입력한 기술이 없습니다."
    experiences = "\n".join(f"• {s}" for s in facts["experiences"]) or "아직 입력한 경험이 없습니다."
    return f"목표 직무 · {role}\n보유 기술 · {skills}\n직접 맡은 일\n{experiences}"


def _verified_courses(recommendations: list[dict], missing: list[dict]) -> list[dict]:
    """수요 기준이 같은 추천에서 표시 가능한 과정만 추출한다."""
    demand = {row["skill"]: row for row in missing}
    courses = []
    for rec in recommendations:
        skill = rec["gap_skill"]
        if skill not in demand or any(
            rec["demand"][k] != demand[skill][k] for k in ("postings", "of", "required_ratio")
        ):
            raise ValueError("추천과 분석의 수요 기준 불일치")
        courses.extend(_verified_course_cards(skill, rec["courses"]))
    return courses


def _course_info(course: dict) -> dict:
    """소요 시간·강의 언어·다루는 AWS 서비스(③ 의 선택 필드). 형식이 맞는 값만 카드에 옮기고 틀리면 그 값만 뺀다."""
    info = {}
    minutes, language, services = course.get("duration_min"), course.get("language"), course.get("aws_services")
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        info["duration_min"] = minutes
    if isinstance(language, str) and language.strip():
        info["language"] = language.strip()
    if isinstance(services, list) and services and all(isinstance(s, str) and s.strip() for s in services):
        info["aws_services"] = [s.strip() for s in services]
    return info


def _verified_course_cards(skill: str, candidates: list[dict]) -> list[dict]:
    """후보 필드와 링크를 검사하고 검증된 이유를 그대로 카드에 옮긴다."""
    courses = []
    for course in candidates:
        keys = ("title", "url", "level", "prerequisites", "reason", "match")
        if any(not isinstance(course.get(k), str) or not course[k].strip() for k in keys):
            raise ValueError("과정 필드 누락")
        url = urlsplit(course["url"])
        if url.scheme != "https" or not url.hostname or course["match"] not in {"certain", "uncertain"}:
            raise ValueError("과정 링크 또는 매칭 상태 오류")
        shown = {**{k: course[k] for k in keys}, "gap_skill": skill}
        avg, count = course.get("rating_avg"), course.get("rating_count")
        # 평점은 선택 필드다. 숫자가 아니거나 범위를 벗어나면 과정은 보여 주되 평점만 뺀다
        if (isinstance(avg, (int, float)) and not isinstance(avg, bool) and 0 < avg <= 5
                and isinstance(count, int) and not isinstance(count, bool) and count > 0):
            shown.update(rating_avg=avg, rating_count=count)
        shown.update(_course_info(course))
        courses.append(shown)
    return courses


def _gap_course_cards(recs: list[dict], rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep verified skills when another worker omits a result or returns invalid data."""
    cards = []
    verified = set()
    for rec in recs:
        try:
            cards.extend(_verified_courses([rec], rows))
            verified.add(rec["gap_skill"])
        except (KeyError, TypeError, ValueError):
            continue
    return cards, [row["skill"] for row in rows if row["skill"] not in verified]


def _compare_structures(pair: tuple[str, str], job: str, rows: list[dict], evidence: dict[str, dict],
                        levels: dict[str, float], *,
                        course_levels: tuple[str, ...] | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """추천 구조 두 개로 각각 추천을 만들고 같은 검사를 한다. (나란히 보여 줄 두 결과, 기본으로 보여 줄 과정)을 돌려준다.

    둘 다 과정을 냈을 때만 A/B 로 보여 준다. 그렇지 않으면 기본 구조(검토를 거친 쪽)의 결과만 보여 준다.
    검토를 거치지 않은 구조의 추천은 A/B 밖에서 혼자 나가지 않는다. 기본 구조가 검사에 실패했으면 그 오류를 낸다.
    구조별 토큰은 세션 사용량의 앞뒤 차이로 잰다.
    """
    usage = current_session()
    results, unavailable = [], {}
    default_error = None
    for name in pair:
        before = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
        try:
            options = {"course_levels": course_levels} if course_levels is not None else {}
            recs = experiments.recommend_with(name, job, list(evidence), evidence=evidence, levels=levels, **options)
            found, unavailable[name] = _gap_course_cards(recs, rows)
        except Exception as exc:
            found, unavailable[name] = [], list(evidence)
            if name == experiments.DEFAULT_STRUCTURE:
                default_error = exc
        after = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
        results.append({"structure": name, "courses": found,
                        "tokens": {"input": after[0] - before[0], "output": after[1] - before[1]}})
    default = next((r for r in results if r["structure"] == experiments.DEFAULT_STRUCTURE), None)
    if all(r["courses"] for r in results):
        failed = [skill for skill in evidence if any(skill in missing for missing in unavailable.values())]
        return results, (default or results[0])["courses"], failed
    if default is None:  # 기본 구조가 비교 대상에 없으면 A/B 가 성립할 때만 보여 준다
        return [], [], list(evidence)
    if default_error is not None:
        raise default_error
    return [], default["courses"], unavailable[experiments.DEFAULT_STRUCTURE]


def _analyze(facts: dict, levels: dict[str, int], *, courses: bool, supervised: bool = False,
             course_levels: tuple[str, ...] | None = None, gap_rank: int | None = None) -> Reply:
    job = facts["job"]
    try:
        rows = api.get_gap(job, levels)
        unrated = [name for name in facts["skills"] if not has_skill_rating(levels, name, job)]
        notes = ["수준 미입력 · " + ", ".join(unrated) + " (수준 입력 후 비교합니다.)"] if unrated else []
        if not rows:
            message = ("현재 수요 기준을 충족하는 비교 대상 기술이 없습니다." if api.get_demand(job)["total_postings"]
                       else "수요 데이터가 없어 현재 역량과 비교할 수 없습니다.")
            return Reply("\n".join([message, *notes]))
    except Exception:
        return Reply("채용공고 수요를 불러오지 못해 부족 역량을 비교할 수 없습니다.", failed=True)
    excluded = {name.casefold() for name in facts["excluded_skills"]}
    conflicts = [r["skill"] for r in rows if _skill(r["skill"]).casefold() in excluded and r.get("user_level", 0) > 0]
    if conflicts:
        notes.append("비교 보류 · " + ", ".join(conflicts)
                     + " (입력한 수준과 이번 상담의 정정 내용이 다릅니다. 보유 수준을 수정해 주세요.)")
    deferred = {_skill(name).casefold() for name in [*unrated, *conflicts]}
    comparable = [row for row in rows if _skill(row["skill"]).casefold() not in deferred]
    if not comparable:
        return Reply("\n".join(notes))
    missing = [row for row in comparable if not row["owned"]]
    if not missing:
        if not notes:
            return Reply("입력한 보유 수준이 현재 공고의 기술 기준을 모두 충족합니다.")
        return Reply("\n".join(["현재 비교한 항목 중 기준에 미달한 기술은 없습니다.", *notes]))
    if not courses:
        lines = [f"{JOBS[job]} 공고의 기준 단계에 미달한 기술입니다."]
        for row in missing:
            line = f"• {row['skill']} · 공고 {row['of']}건 중 {row['postings']}건"
            if "user_level" in row and "required_level" in row and has_skill_rating(levels, row["skill"], job):
                line += f" · 내 수준 {row['user_level']}단계 / 기준 {row['required_level']}단계"
            lines.append(line)
        return Reply("\n".join([*lines, *notes]), gap_skills=[row["skill"] for row in missing])

    if gap_rank is None:
        rows = missing[:api.RECOMMEND_TOP_N]
    elif gap_rank == -1 or 1 <= gap_rank <= len(missing):
        rows = [missing[-1 if gap_rank == -1 else gap_rank - 1]]
    else:
        return Reply(f"{JOBS[job]}에서 비교한 부족 기술은 {len(missing)}개여서 요청한 순번을 찾지 못했습니다.")
    requested = [row["skill"] for row in rows]
    reply = Reply("", course_targets=requested)
    try:
        evidence = {row["skill"]: {key: row[key] for key in ("postings", "of", "required_ratio")} for row in rows}
        known_levels = {row["skill"]: row["user_level"] for row in rows if has_skill_rating(levels, row["skill"], job)}
        options = {"course_levels": course_levels} if course_levels is not None else {}
        pair = None if supervised else experiments.pick_pair()
        if pair:
            reply.alternatives, reply.courses, unavailable = _compare_structures(pair, job, rows, evidence, known_levels,
                                                                               **options)
        else:
            recs = api.get_recommendations(job, requested, review=True, levels=known_levels, review_evidence=evidence,
                                           **options)
            reply.courses, unavailable = _gap_course_cards(recs, rows)
        if reply.alternatives:
            detail = "두 가지 방식으로 교육과정을 추천했습니다. 더 도움이 되는 쪽을 골라 주세요."
        elif reply.courses:
            skills = ", ".join(dict.fromkeys(card["gap_skill"] for card in reply.courses))
            detail = f"{skills} 관련 교육과정을 추천합니다."
        elif len(unavailable) == len(requested):
            detail = "요청한 기술의 교육과정을 검증하지 못했습니다."
        else:
            checked = ", ".join(skill for skill in requested if skill not in unavailable)
            detail = f"{checked}에 연결할 교육과정을 찾지 못했습니다."
        if course_levels and len(unavailable) < len(requested):
            shown = {card["gap_skill"] for card in reply.courses}
            empty = [name for name in requested if name not in shown and name not in unavailable]
            if empty:
                message = f"{', '.join(empty)}에서 요청한 {'·'.join(course_levels)} 과정을 찾지 못했습니다."
                if reply.courses:
                    notes.append(message)
                else:
                    detail = message
        if unavailable:
            notes.append("추천 결과를 확인하지 못한 기술 · " + ", ".join(unavailable))
            reply.failed = True
    except (BedrockJSONError, KeyError, TypeError, ValueError):
        detail = f"{', '.join(requested)} 관련 교육과정을 검증하지 못했습니다."
        reply.failed = True
    except Exception:
        detail = f"{', '.join(requested)} 관련 교육과정을 불러오지 못했습니다."
        reply.failed = True
    reply.text = "\n".join([detail, *notes])
    return reply


def _requirements(job: str, message: str, turn: intake.Turn) -> Reply:
    try:
        demand = api.get_demand(job)
    except Exception:
        return Reply("채용공고 수요를 불러오지 못해 필수·우대 건수를 확인할 수 없습니다.", failed=True)
    rows = {_skill(row["skill"]): row for row in demand["skills"]}
    if turn.add_skills or turn.add_experiences:
        message = intake.requirement_question(message)
    names = intake.target_skills(message, list(rows))
    if not names:
        return Reply("어떤 기술이 필요한지 궁금하신가요? 비교하려는 기술 이름을 함께 알려 주세요.")
    if not demand["total_postings"]:
        return Reply(f"{JOBS[job]} 직무에서 분석한 공고가 없어 {', '.join(names)}의 요구 수준을 확인할 수 없습니다.")

    lines = [f"{JOBS[job]} 공고 {demand['total_postings']}건에서 확인한 내용입니다."]
    for name in names:
        row = rows.get(name)
        if row:
            lines.append(f"• {name} · {row['postings']}건에서 언급 (필수 {row['required']}건, 우대 {row['preferred']}건)")
        else:
            lines.append(f"• {name} · 해당 기술의 필수·우대 집계가 없습니다.")
    lines.append("필요한 경험의 깊이는 지원할 공고의 자격 요건을 확인하세요. 여러 도구 중 하나를 쓸 수 있으면 되는 "
                 "공고에는 익숙한 도구로 수행한 프로젝트를 제시할 수 있습니다. 특정 기술을 필수로 지정했다면 "
                 "그 기술로 어떤 작업을 해 봤는지가 중요합니다.")
    return Reply("\n".join(lines))


def _requested_course_levels(preference: str | None, previous: list[str]) -> tuple[str, ...] | None:
    """Resolve a relative request against the levels actually shown for this skill."""
    if preference is None:
        return None
    if preference in COURSE_LEVELS:
        return (preference,)
    if preference == "above_basic":
        return COURSE_LEVELS[1:]
    if preference == "below_advanced":
        return COURSE_LEVELS[-2::-1]
    ranks = [COURSE_LEVELS.index(level) for level in previous if level in COURSE_LEVELS]
    if preference == "harder":
        return COURSE_LEVELS[max(ranks, default=0) + 1:]
    if preference == "easier":
        return COURSE_LEVELS[:min(ranks, default=len(COURSE_LEVELS) - 1)][::-1]
    raise ValueError("지원하지 않는 과정 난이도입니다.")


def _targeted_courses(facts: dict, levels: dict, targets: list[str], preference: str | None, *,
                      supervised: bool) -> Reply:
    from backend.supervised_courses import recommend_for_targets

    def allowed(skill: str) -> tuple[str, ...] | None:
        return _requested_course_levels(preference, facts["last_course_levels"].get(skill, []))

    pair = None if supervised else experiments.pick_pair()
    groups = ([(None, targets)] if preference is None
              else groupby(targets[:api.RECOMMEND_TOP_N], key=allowed))
    replies = []
    for wanted, group in groups:
        names = list(group)
        if wanted == ():
            limit = "고급보다 어려운" if preference == "harder" else "기초보다 쉬운"
            replies.append(Reply(f"{', '.join(names)}에는 {limit} 난이도가 없습니다.", action="courses"))
            continue
        options = {"course_levels": wanted} if wanted is not None else {}
        if pair:
            options["pair"] = pair
        replies.append(recommend_for_targets(facts["job"], names, levels, facts, **options))
    if len(replies) == 1:
        result = replies[0]
    else:
        result = Reply("\n".join(part.text for part in replies), action="courses",
                       courses=[card for part in replies for card in part.courses],
                       failed=any(part.failed for part in replies))
        # Only open a vote if both sides cover all groups with displayed cards.
        with_cards = [part for part in replies if part.courses]
        if pair and with_cards and all(part.alternatives for part in with_cards):
            for index, structure in enumerate(pair):
                sides = [part.alternatives[index] for part in with_cards]
                result.alternatives.append({
                    "structure": structure, "courses": [c for side in sides for c in side["courses"]],
                    "tokens": {key: sum(side["tokens"][key] for side in sides) for key in ("input", "output")},
                })
    if result.alternatives:
        result.text = "두 가지 방식으로 교육과정을 추천했습니다. 더 도움이 되는 쪽을 골라 주세요.\n" + result.text
    if preference is not None and len(targets) > api.RECOMMEND_TOP_N:
        result.text += f"\n다음 요청에서 조회할 기술 · {', '.join(targets[api.RECOMMEND_TOP_N:])}"
    return result


def _remember_courses(state: Conversation, facts: dict, reply: Reply, targets: list[str]) -> None:
    """Use displayed card levels as the baseline, including both sides of a vote."""
    cards = list(reply.courses)
    for side in reply.alternatives:
        cards.extend(side["courses"])
    shown: dict[str, list[str]] = {}
    for card in cards:
        levels = shown.setdefault(card["gap_skill"], [])
        level = card.get("level")
        if level in COURSE_LEVELS and level not in levels:
            levels.append(level)
    if reply.action == "clarify" or (not shown and reply.failed):
        return
    previous = facts["last_course_levels"]
    state.last_course_job = facts["job"]
    names = list(dict.fromkeys([*shown, *targets]))[:api.RECOMMEND_TOP_N]
    state.last_course_levels = {name: shown.get(name, previous.get(name, [])) for name in names}


def _review_analysis(message: str, facts: dict, plan: SupervisedTurn, reply: Reply) -> Reply:
    from ai import supervisor

    if reply.failed:
        reply.supervisor_decisions.append("worker_error")
    try:
        approved = supervisor.approve_result(message, facts, plan, reply.text, reply.courses)
    except intake.ChatIntakeError:
        return Reply("상담 결과 검토를 완료하지 못했습니다. 잠시 후 다시 요청해 주세요.",
                     failed=True, supervisor_decisions=[*reply.supervisor_decisions, "result_error"])
    if not approved:
        if reply.failed:
            return Reply("요청하신 분석이나 추천을 검증하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                         failed=True, supervisor_decisions=[*reply.supervisor_decisions, "result_rejected"])
        return Reply("요청하신 내용과 분석 결과가 일치하는지 확인하지 못했습니다. "
                     "비교할 역량이나 원하는 교육과정을 다시 말씀해 주세요.",
                     action="clarify", supervisor_decisions=[*reply.supervisor_decisions, "result_rejected"])
    reply.supervisor_decisions.append("result_approved")
    return reply


def _answer(state: Conversation, turn: intake.Turn, job: str, base_skills: list[str], levels: dict[str, int],
            *, plan: SupervisedTurn | None = None, message: str = "", prior_context: dict | None = None) -> Reply:
    supported = ", ".join(JOBS.values())
    if turn.status == "unsupported" or (turn.job and turn.job not in JOBS):
        state.needs_role = True
        return Reply(f"현재는 {supported} 직무의 역량 분석과 교육과정 추천을 제공합니다.")
    if turn.status == "clarify_role":
        state.needs_role = True
        if len(JOBS) > 1:
            return Reply(f"{' 또는 '.join(JOBS.values())} 중 어느 직무를 기준으로 상담할까요?")
        return Reply(f"{supported} 직무를 기준으로 상담할까요?")
    if turn.status == "incomplete":
        return Reply("이어서 말씀해 주세요.")
    if turn.status != "ok":
        return Reply(f"{supported} 직무 준비와 본인의 역량에 관한 질문을 도와드릴 수 있습니다.")
    _apply_turn(state, turn)
    facts = _context(state, job, base_skills)
    # 저장한 경험 자체에서 확인한 기술은 이번 답변의 비교에만 쓴다.
    facts["skills"] = _effective([*facts["skills"], *turn.experience_skills], state.skills)
    previous_wishes = getattr(state, "wishes", [])
    if plan is not None:
        state.wishes = list(plan.wishes)
        facts["wishes"] = state.wishes
    analysis = turn.action in {"gap", "requirements", "courses"}
    targets = []
    if analysis:
        if not facts["job"]:
            reply = Reply(f"{supported} 직무 기준으로 비교할까요?")
        elif turn.action == "requirements":
            reply = _requirements(facts["job"], message, turn)
        elif turn.action == "gap":
            reply = _analyze(facts, levels, courses=False)
        else:
            difficulty = turn.course_difficulty
            gap_rank = None
            if plan is not None:
                from ai import supervisor

                targets = plan.targets
                reference = supervisor.switched_course_reference(message, prior_context or {}, turn)
                if reference and reference[0] == "gap":
                    gap_rank = reference[2]
                    targets = []
            else:
                targets = intake.explicit_course_skills(message, facts["skills"])
                if not targets and difficulty and not intake.requests_gap_courses(message):
                    targets = facts["last_course_skills"]
            excluded = (intake.excluded_course_skills(message, facts["last_course_skills"])
                        if difficulty and not intake.requests_gap_courses(message) else set())
            if excluded:
                previous_or_planned = targets or facts["last_course_skills"]
                targets = [name for name in previous_or_planned if _skill(name).casefold() not in excluded]
                if plan is not None:
                    plan.targets = targets
            if targets:
                reply = _targeted_courses(facts, levels, targets, difficulty, supervised=plan is not None)
            elif excluded:
                reply = Reply("직전 추천에서 제외한 기술 외에 어떤 기술의 과정을 찾아볼까요?", action="clarify")
            else:
                reply = _analyze(facts, levels, courses=True, supervised=plan is not None,
                                 course_levels=_requested_course_levels(difficulty, []), gap_rank=gap_rank)
                targets = reply.course_targets
                if gap_rank is not None:
                    plan.targets = targets
                    if not targets:
                        reply.action = "clarify"
                        reply.text += "\n" + supervisor.COURSE_QUESTION
    elif turn.action == "summary":
        reply = Reply(_summary(facts))
        if plan is not None and state.wishes:
            reply.text += "\n학습 희망 기술 · " + ", ".join(state.wishes)
    else:
        updates = []
        if turn.job:
            updates.append(f"{JOBS[turn.job]} 기준으로 함께 볼게요.")
        if turn.add_skills:
            updates.append(f"{', '.join(turn.add_skills)} 사용 경험이 있으시군요.")
        if turn.add_experiences:
            updates.append("직접 맡으신 작업 · " + ", ".join(turn.add_experiences))
        if turn.remove_skills or turn.remove_experiences:
            items = dict.fromkeys([*turn.remove_skills, *turn.remove_experiences])
            updates.append("이번 상담 정보에서 제외했습니다. " + ", ".join(items))
        if plan is not None and state.wishes != previous_wishes:
            updates.append("학습 희망 기술 · " + (", ".join(state.wishes) or "없음"))
        reply = Reply("\n".join(updates) or "궁금한 기술이나 준비 중인 프로젝트에 대해 말씀해 주세요.")
        if not turn.save:
            try:
                reply.text = chat_reply.write_reply(
                    message, action=turn.action, draft=reply.text, history=state.history, confirmed_skills=facts["skills"],
                )
            except chat_reply.ReplyBlocked as exc:
                return Reply(str(exc), action="blocked")
    if analysis and facts["job"] and plan is not None:
        review_context = {**(prior_context or {}), **facts}
        if prior_context and prior_context["job"] != facts["job"]:
            review_context["last_gap_skills"] = []
        reply = _review_analysis(message, review_context, plan, reply)
        if turn.action == "gap" or reply.gap_skills:
            state.last_gap_job = facts["job"]
            state.last_gap_skills = list(reply.gap_skills)
    if turn.action == "courses" and facts["job"]:
        _remember_courses(state, facts, reply, targets)
    if turn.save:
        saved = _save(state, turn, facts)
        reply.text = saved if turn.action == "note" else reply.text + "\n\n" + saved
    if facts["job"]:
        reply.assessment_skills = [name for name in turn.add_skills if name not in turn.remove_skills]
        if reply.assessment_skills:
            reply.assessment_job = facts["job"]
    return reply


def handle_message(state: Conversation, message: str, *, job: str, sidebar_skills: list[str],
                   session: chat_profiles.ProfileSession | None = None,
                   sidebar_levels: dict[str, int] | None = None, architecture: str = "screen") -> Reply:
    """새 메시지 한 건을 처리한다. 화면 재표시는 이 진입점을 호출하지 않는다."""
    if architecture not in {"screen", "supervised"}:
        raise ValueError("지원하지 않는 상담 구조입니다.")
    supervised = architecture == "supervised"
    previous = getattr(state, "session", None)
    # Open connections may still hold the pre-authentication Conversation type.
    legacy_nickname = vars(state).pop("nickname", "")
    if legacy_nickname or (previous is not None and session is not previous):
        Conversation.__init__(state)
    state.session = session
    model = None if supervised else experiments.pick_model()
    turn_id = uuid4().hex
    before = (state.usage.input_tokens, state.usage.output_tokens)
    rival = None if supervised else experiments.chat_rival()
    snapshot = copy.deepcopy(state) if rival else None  # 비교용 구조는 이 턴 전의 상태에서 같은 메시지를 다시 처리한다
    with track_session(state.usage), use_model(model), lambda_tools.track_turn(turn_id):
        used = "" if api.is_mock() else current_model()
        try:
            reply = _handle_turn(state, message, job, sidebar_skills, sidebar_levels, supervised=supervised)
        except Exception:
            if not supervised:
                _record_turn(state, turn_id, job, used, before, failed=True)
            raise
    reply.turn_id, reply.model = turn_id, used
    if not supervised:
        _record_turn(state, turn_id, job, used, before, action=reply.action, failed=reply.failed)
    if rival and reply.action == "courses" and reply.courses and not reply.alternatives and not reply.failed:
        reply.alternatives = _compare_chat_structures(state, snapshot, rival, message, job, sidebar_skills,
                                                      sidebar_levels, reply, before)
        if reply.alternatives:
            facts = _context(state, job, sidebar_skills)
            _remember_courses(state, facts, reply, facts["last_course_skills"])
    return reply


def _compare_chat_structures(state: Conversation, snapshot: Conversation, rival: str, message: str, job: str,
                             sidebar_skills: list[str], sidebar_levels: dict[str, int] | None, reply: Reply,
                             before: tuple[int, int]) -> list[dict]:
    """과정 추천 턴을 상담 구조(rival, 예: 3번 팀장 구조)로 한 번 더 처리해 A/B 로 나란히 보여 줄 두 결과를 만든다.

    상담 상태와 화면의 답은 기본 구조의 것을 그대로 둔다. 비교용 구조는 턴 전 상태의 사본에서 돌리고 프로필에는 쓰지 못하게
    로그인 정보를 뺀다. 비교용 구조가 과정을 내지 못했거나 검증에 실패하면 빈 목록을 돌려줘 A/B 를 열지 않는다.
    좌우 순서는 코드가 무작위로 정하고, 구조별 토큰은 세션 사용량의 앞뒤 차이로 잰다.
    """
    usage = state.usage
    screen_tokens = {"input": usage.input_tokens - before[0], "output": usage.output_tokens - before[1]}
    snapshot.session = None
    snapshot.profile = copy.deepcopy(state.profile)
    start = (usage.input_tokens, usage.output_tokens)
    try:
        with track_session(usage), use_model(None), lambda_tools.track_turn(reply.turn_id):
            other = _handle_turn(snapshot, message, job, sidebar_skills, sidebar_levels, supervised=True)
    except Exception:
        return []
    if other.failed or other.action != "courses" or not other.courses:
        return []
    results = [
        {"structure": experiments.DEFAULT_STRUCTURE, "courses": reply.courses, "tokens": screen_tokens},
        {"structure": rival, "courses": other.courses,
         "tokens": {"input": usage.input_tokens - start[0], "output": usage.output_tokens - start[1]}},
    ]
    random.shuffle(results)
    return results


def _record_turn(state: Conversation, turn_id: str, job: str, model: str, before: tuple[int, int], *,
                 action: str = "", failed: bool = False) -> None:
    """실험이 켜져 있으면 턴마다 배정된 모델, 요청 종류, 쓴 토큰, 검사 실패 여부를 남긴다(내용은 남기지 않는다).

    평가를 누르지 않은 턴도 모델별 턴 수와 턴당 비용을 세는 데 필요하다. 기록 실패는 상담을 막지 않는다.
    """
    if not experiments.active():
        return
    tokens = {"input": state.usage.input_tokens - before[0], "output": state.usage.output_tokens - before[1]}
    try:
        experiments.save_feedback(state.usage.session_id, turn_id, job=job, model=model or None, action=action or None,
                                  turn_tokens=tokens, failed=True if failed else None)
    except Exception:
        pass


def _handle_turn(state: Conversation, message: str, job: str, sidebar_skills: list[str],
                 sidebar_levels: dict[str, int] | None, *, supervised: bool = False) -> Reply:
    if not message.strip():
        return Reply("이어서 말씀해 주세요.")
    try:
        allowed = intake.guard_input(message)
    except intake.ChatIntakeError:
        return Reply("요청의 가드레일 점검을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.", action="intake", failed=True)
    if not allowed:
        return Reply("이 요청은 학습 상담 범위에서 처리할 수 없습니다. 본인 상황(전공·학력 등)은 말씀하셔도 괜찮아요. 공부 방법이나 준비 과정으로 다시 물어봐 주세요.", action="blocked")
    read_failed = False
    if state.session is not None and state.profile is None:
        try:
            state.profile = chat_profiles.load_profile(state.session)
        except Exception:
            read_failed = True
    # 기술 하나에는 프로필 또는 세션에서 처음 사용한 표기를 쓴다. 경험 문구는 원문을 유지한다.
    spellings: dict[str, str] = {}

    def display_name(raw: str) -> str:
        name = _skill(raw)
        return spellings.setdefault(name.casefold(), name)

    def names(raws: list[str]) -> list[str]:
        return list(dict.fromkeys(map(display_name, raws)))

    base_skills = names((state.profile or {}).get("skills", []))
    state.skills = {display_name(raw): present for raw, present in state.skills.items()}
    base_skills.extend(names(sidebar_skills))
    plan = None
    try:
        context = _context(state, job, base_skills)
        if supervised:
            from ai import supervisor

            context.update(wishes=getattr(state, "wishes", []),
                           pending_course_question=getattr(state, "pending_course_question", False),
                           offered_gap=state.offered_gap,
                           last_gap_skills=(getattr(state, "last_gap_skills", [])
                                            if getattr(state, "last_gap_job", None) == context["job"] else []))
            plan = supervisor.interpret_turn(message, context)
            turn = plan.turn
        else:
            turn = intake.interpret(message, context, screened=True)
        if (turn.job and turn.job == context["job"] and turn.add_skills
                and not any(cue in message.casefold() for cue in (JOBS[turn.job].casefold(), "목표", "직무", "기준"))):
            turn = replace(turn, job=None)
        turn = replace(turn, add_skills=names(turn.add_skills), remove_skills=names(turn.remove_skills),
                       experience_skills=names(turn.experience_skills), save_skills=names(turn.save_skills))
        if plan is not None:
            plan.turn = turn
            if plan.question:
                _apply_turn(state, turn)
                state.pending_save = False
                state.offered_gap = False
                state.pending_course_question = plan.question == supervisor.COURSE_QUESTION
                removed = dict.fromkeys([*turn.remove_skills, *turn.remove_experiences])
                text = plan.question
                if removed:
                    text = "이번 상담 정보에서 제외했습니다. " + ", ".join(removed) + "\n" + text
                _remember_reply(state, message, text)
                return Reply(text, action="clarify", supervisor_decisions=list(plan.decisions))
    except intake.ChatIntakeError:
        return Reply("메시지를 해석하지 못했습니다. 요청을 다시 말씀해 주세요.", action="intake", failed=True)
    if (not supervised and state.offered_gap and turn.status == "ok" and turn.action == "note"
            and message.strip().rstrip(".!").strip() in {"네", "예", "응", "좋아요", "비교해줘", "비교해 주세요"}):
        turn.action = "gap"
    if turn.status not in {"blocked", "out_of_scope"}:
        state.pending_save = False
        state.offered_gap = False
        state.pending_course_question = False
    levels = (state.profile or {}).get("levels", {}) if sidebar_levels is None else sidebar_levels
    reply = _answer(state, turn, job, base_skills, levels, plan=plan, message=message, prior_context=context)
    reply.action = reply.action or (turn.action if turn.status == "ok" else turn.status)
    if plan is not None:
        reply.supervisor_decisions = [*plan.decisions, *reply.supervisor_decisions]
        if reply.action == "clarify" and turn.action == "courses":
            state.pending_course_question = True
    if reply.action == "blocked":
        return reply
    if turn.status == "ok" and turn.action == "summary" and "상태" in message and not any(
        word in message for word in ("정리", "요약", "만")
    ):
        state.offered_gap = True
        role = JOBS.get(context["job"], "지원 직무")
        reply.text += f"\n\n{role} 공고에서 요구하는 기술과도 비교할까요?"
    if turn.status in {"ok", "unsupported", "clarify_role", "incomplete"}:
        _remember_reply(state, message, reply.text)
    if read_failed and state.profile is None:
        reply.text += "\n\n저장된 프로필을 불러오지 못해 이번 상담에서 확인한 정보만 사용했습니다."
    return reply


def _remember_reply(state: Conversation, message: str, text: str) -> None:
    state.history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": text}])
    state.history = state.history[-8:]
