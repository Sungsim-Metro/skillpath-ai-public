"""학습 상담 상태를 합치고, 사용자가 요청한 범위의 도구만 실행한다."""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from ai import chat as intake
from ai.bedrock import BedrockJSONError, current_model, use_model
from ai.usage import SessionUsage, current_session, track_session
from backend import api, chat_profiles, experiments
from backend.jobs import JOBS
from backend.levels import has_skill_rating
from pipeline.aliases import normalize_skill

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


def _skill(name: str) -> str:
    return normalize_skill(name) or name.strip()


def _effective(base: list[str], edits: dict[str, bool]) -> list[str]:
    merged = dict.fromkeys(base, True)
    merged.update(edits)
    return [name for name, present in merged.items() if present]


def _context(state: Conversation, sidebar_job: str, base_skills: list[str]) -> dict:
    profile = state.profile or {}
    job = state.job or profile.get("job") or sidebar_job
    return {
        "job": job if job in JOBS and not state.needs_role else None,
        "needs_role": state.needs_role,
        "pending_save": state.pending_save,
        "skills": _effective(base_skills, state.skills),
        "excluded_skills": [skill for skill, present in state.skills.items() if not present],
        "experiences": _effective(profile.get("experiences", []), state.experiences),
        "history": state.history,
        "last_changes": state.last_changes,
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
        courses.append(shown)
    return courses


def _compare_structures(pair: tuple[str, str], job: str, requested: list[str],
                        missing: list[dict]) -> tuple[list[dict], list[dict]]:
    """추천 구조 두 개로 각각 추천을 만들고 같은 검사를 한다. (나란히 보여 줄 두 결과, 기본으로 보여 줄 과정)을 돌려준다.

    둘 다 과정을 냈을 때만 A/B 로 보여 준다. 그렇지 않으면 기본 구조(검토를 거친 쪽)의 결과만 보여 준다.
    검토를 거치지 않은 구조의 추천은 A/B 밖에서 혼자 나가지 않는다. 기본 구조가 검사에 실패했으면 그 오류를 낸다.
    구조별 토큰은 세션 사용량의 앞뒤 차이로 잰다.
    """
    usage = current_session()
    results, error = [], None
    for name in pair:
        before = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
        try:
            found = _verified_courses(experiments.recommend_with(name, job, requested), missing)
        except (BedrockJSONError, KeyError, TypeError, ValueError) as exc:
            found, error = [], exc
        after = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
        results.append({"structure": name, "courses": found,
                        "tokens": {"input": after[0] - before[0], "output": after[1] - before[1]}})
    default = next((r for r in results if r["structure"] == experiments.DEFAULT_STRUCTURE), None)
    if all(r["courses"] for r in results):
        return results, (default or results[0])["courses"]
    if default is None:  # 기본 구조가 비교 대상에 없으면 A/B 가 성립할 때만 보여 준다
        default = {"courses": []}
    if not default["courses"] and error is not None:
        raise error
    return [], default["courses"]


def _analyze(facts: dict, levels: dict[str, int], *, courses: bool, supervised: bool = False) -> Reply:
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
        return Reply("채용공고 수요를 불러오지 못해 부족 역량을 비교할 수 없습니다.", failed=supervised)
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
    lines = [f"{JOBS[job]} 공고의 기준 단계에 미달한 기술입니다."]
    for row in missing:
        line = f"• {row['skill']} · 공고 {row['of']}건 중 {row['postings']}건"
        if "user_level" in row and "required_level" in row and has_skill_rating(levels, row["skill"], job):
            line += f" · 내 수준 {row['user_level']}단계 / 기준 {row['required_level']}단계"
        lines.append(line)
    lines.extend(notes)
    reply = Reply("\n".join(lines), gap_skills=[row["skill"] for row in missing])
    if courses:
        try:
            requested = api.pick_recommendable([r["skill"] for r in missing], review=True)
            rows = [r for r in missing if r["skill"] in requested]  # 수요 순서 그대로, 추천 대상 행
            last = max((i for i, r in enumerate(missing) if r["skill"] in requested), default=-1)
            skipped = [r["skill"] for r in missing[:last] if r["skill"] not in requested]
            pair = None if supervised else experiments.pick_pair()
            unavailable_skills = []
            if pair:
                reply.alternatives, reply.courses = _compare_structures(pair, job, requested, rows)
            else:
                if supervised:
                    known_levels = {r["skill"]: r["user_level"] for r in rows
                                    if has_skill_rating(levels, r["skill"], job)}
                    recs = api.get_recommendations(job, requested, review=True, levels=known_levels)
                    returned = {rec["gap_skill"] for rec in recs}
                    unavailable_skills = [skill for skill in requested if skill not in returned]
                else:
                    recs = api.get_recommendations(job, requested, review=True)
                reply.courses = _verified_courses(recs, rows)
            detail = ("부족 기술과 연결된 과정입니다. 과정별 추천 이유와 매칭 상태를 확인해 주세요." if reply.courses
                      else "상위 부족 기술에 연결할 교육과정을 찾지 못했습니다.")
            if reply.alternatives:
                detail = ("부족 기술과 연결된 과정을 두 가지 방식으로 추천했습니다. "
                          "과정별 추천 이유를 읽고 더 도움이 되는 쪽을 골라 주세요.")
            if skipped and reply.courses:
                detail += "\n연결할 교육과정이 없어 건너뛴 기술 · " + ", ".join(skipped)
            if unavailable_skills:
                detail += "\n추천 결과를 확인하지 못한 기술 · " + ", ".join(unavailable_skills)
                reply.failed = True
        except (BedrockJSONError, KeyError, TypeError, ValueError):
            detail = "추천 결과가 검증을 통과하지 못해 과정을 표시할 수 없습니다."
            reply.failed = True
        except Exception:
            detail = "교육과정 추천을 불러오지 못했습니다."
            reply.failed = supervised
        reply.text += "\n\n" + detail
    return reply


def _supervised_analysis(message: str, facts: dict, levels: dict, plan: SupervisedTurn) -> Reply:
    from ai import supervisor
    from backend.supervised_courses import recommend_for_targets

    if plan.turn.action == "courses" and plan.targets:
        reply = recommend_for_targets(facts["job"], plan.targets, levels, facts)
    else:
        reply = _analyze(facts, levels, courses=plan.turn.action == "courses", supervised=True)
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
    if turn.action in {"gap", "courses"}:
        if not facts["job"]:
            reply = Reply(f"{supported} 직무 기준으로 비교할까요?")
        elif plan is not None:
            reply = _supervised_analysis(message, {**(prior_context or {}), **facts}, levels, plan)
            if turn.action == "gap" or reply.gap_skills:
                state.last_gap_job = facts["job"]
                state.last_gap_skills = list(reply.gap_skills)
        else:
            reply = _analyze(facts, levels, courses=turn.action == "courses")
    elif turn.action == "summary":
        reply = Reply(_summary(facts))
        if plan is not None and state.wishes:
            reply.text += "\n학습 희망 기술 · " + ", ".join(state.wishes)
    else:
        updates = []
        if turn.job:
            updates.append(f"목표 직무를 {JOBS[turn.job]}로 반영했습니다.")
        if turn.add_skills or turn.add_experiences:
            items = dict.fromkeys([*turn.add_skills, *turn.add_experiences])
            updates.append("이번 상담에 반영했습니다. " + ", ".join(items))
        if turn.remove_skills or turn.remove_experiences:
            items = dict.fromkeys([*turn.remove_skills, *turn.remove_experiences])
            updates.append("이번 상담 정보에서 제외했습니다. " + ", ".join(items))
        if plan is not None and state.wishes != previous_wishes:
            updates.append("학습 희망 기술 · " + (", ".join(state.wishes) or "없음"))
        reply = Reply("\n".join(updates) or "현재 보유한 기술이나 프로젝트에서 직접 맡은 일을 말씀해 주세요.")
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
    with track_session(state.usage), use_model(model):
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
        with track_session(usage), use_model(None):
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
        return Reply("이 요청은 학습 상담 범위에서 처리할 수 없습니다.", action="blocked")
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
            if plan.question:
                state.pending_save = False
                state.offered_gap = False
                state.pending_course_question = plan.question == supervisor.COURSE_QUESTION
                _remember_reply(state, message, plan.question)
                return Reply(plan.question, action="clarify", supervisor_decisions=list(plan.decisions))
            turn = plan.turn
        else:
            turn = intake.interpret(message, context, screened=True)
        turn = replace(turn, add_skills=names(turn.add_skills), remove_skills=names(turn.remove_skills),
                       experience_skills=names(turn.experience_skills), save_skills=names(turn.save_skills))
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
