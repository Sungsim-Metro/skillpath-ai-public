"""학습 상담 상태를 합치고, 사용자가 요청한 범위의 도구만 실행한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from ai import chat as intake
from ai.bedrock import BedrockJSONError, current_model, use_model
from ai.usage import SessionUsage, current_session, track_session
from backend import api, chat_profiles, experiments
from backend.jobs import JOBS
from pipeline.aliases import normalize_skill


@dataclass
class Conversation:
    """접속 중인 한 이용자의 대화 상태. False인 수정값은 이전 보유 진술을 취소한다."""

    nickname: str = ""
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


def _skill(name: str) -> str:
    return normalize_skill(name) or name.strip()


def _effective(base: list[str], edits: dict[str, bool]) -> list[str]:
    merged = dict.fromkeys(base, True)
    merged.update(edits)
    return [name for name, present in merged.items() if present]


def _context(state: Conversation, sidebar_job: str, sidebar_skills: list[str]) -> dict:
    profile = state.profile or {}
    skills = [_skill(s) for s in [*profile.get("skills", []), *sidebar_skills]]
    job = state.job or profile.get("job") or sidebar_job
    return {
        "job": job if job in JOBS and not state.needs_role else None,
        "needs_role": state.needs_role,
        "pending_save": state.pending_save,
        "skills": _effective(skills, state.skills),
        "excluded_skills": [skill for skill, present in state.skills.items() if not present],
        "experiences": _effective(profile.get("experiences", []), state.experiences),
        "history": state.history,
        "last_changes": state.last_changes,
    }


def _apply_turn(state: Conversation, turn: intake.Turn) -> None:
    added = [_skill(x) for x in turn.add_skills]
    removed = [_skill(x) for x in turn.remove_skills]
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
        ("skills", [_skill(s) for s in turn.save_skills], state.skills),
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
            changes.setdefault(f"{op}_{key}", []).append(item)
            labels.append(item if op == "add" else f"{item} (삭제)")
    if not changes:
        state.pending_save = True
        return "프로필에 저장할 항목을 지정해 주세요."
    if not state.nickname:
        return "프로필을 저장하려면 닉네임을 입력한 뒤 저장할 항목을 다시 말씀해 주세요."
    try:
        state.profile = chat_profiles.update_profile(state.nickname, **changes)
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
        for course in rec["courses"]:
            keys = ("title", "url", "level", "prerequisites", "reason", "match")
            if any(not isinstance(course.get(k), str) or not course[k].strip() for k in keys):
                raise ValueError("과정 필드 누락")
            url = urlsplit(course["url"])
            if url.scheme != "https" or not url.hostname or course["match"] not in {"certain", "uncertain"}:
                raise ValueError("과정 링크 또는 매칭 상태 오류")
            courses.append({**{k: course[k] for k in keys}, "gap_skill": skill})
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


def _analyze(facts: dict, *, courses: bool) -> Reply:
    job = facts["job"]
    try:
        rows = api.get_gap(job, facts["skills"])
        if not rows:
            if not api.get_demand(job)["total_postings"]:
                return Reply("수요 데이터가 없어 현재 역량과 비교할 수 없습니다.")
            return Reply("현재 수요 기준을 충족하는 비교 대상 기술이 없습니다.")
    except Exception:
        return Reply("채용공고 수요를 불러오지 못해 부족 역량을 비교할 수 없습니다.")
    missing = [row for row in rows if not row["owned"]]
    if not missing:
        return Reply("입력한 보유 기술이 현재 공고 수요 기준의 기술을 모두 포함합니다.")
    lines = [f"입력한 보유 기술을 기준으로 {JOBS[job]} 공고에서 추가로 요구하는 기술입니다."]
    lines.extend(f"• {r['skill']} · 공고 {r['of']}건 중 {r['postings']}건" for r in missing)
    reply = Reply("\n".join(lines))
    if courses:
        try:
            requested = [r["skill"] for r in missing][:api.RECOMMEND_TOP_N]
            pair = experiments.pick_pair()
            if pair:
                reply.alternatives, reply.courses = _compare_structures(pair, job, requested, missing[:len(requested)])
            else:
                recs = api.get_recommendations(job, requested, review=True)
                reply.courses = _verified_courses(recs, missing[:len(requested)])
            detail = ("부족 기술과 연결된 과정입니다. 과정별 추천 이유와 매칭 상태를 확인해 주세요." if reply.courses
                      else "상위 부족 기술에 연결할 교육과정을 찾지 못했습니다.")
            if reply.alternatives:
                detail = ("부족 기술과 연결된 과정을 두 가지 방식으로 추천했습니다. "
                          "과정별 추천 이유를 읽고 더 도움이 되는 쪽을 골라 주세요.")
        except (BedrockJSONError, KeyError, TypeError, ValueError):
            detail = "추천 결과가 검증을 통과하지 못해 과정을 표시할 수 없습니다."
            reply.failed = True
        except Exception:
            detail = "교육과정 추천을 불러오지 못했습니다."
        reply.text += "\n\n" + detail
    return reply


def _answer(state: Conversation, turn: intake.Turn, job: str, sidebar_skills: list[str]) -> Reply:
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
    facts = _context(state, job, sidebar_skills)
    # 저장한 경험 자체에서 확인한 기술은 이번 답변의 비교에만 쓴다.
    facts["skills"] = _effective([*facts["skills"], *map(_skill, turn.experience_skills)], state.skills)
    if turn.action in {"gap", "courses"}:
        if not facts["job"]:
            reply = Reply(f"{supported} 직무 기준으로 비교할까요?")
        else:
            reply = _analyze(facts, courses=turn.action == "courses")
    elif turn.action == "summary":
        reply = Reply(_summary(facts))
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
        reply = Reply("\n".join(updates) or "현재 보유한 기술이나 프로젝트에서 직접 맡은 일을 말씀해 주세요.")
    if turn.save:
        saved = _save(state, turn, facts)
        reply.text = saved if turn.action == "note" else reply.text + "\n\n" + saved
    return reply


def handle_message(state: Conversation, message: str, *, job: str, sidebar_skills: list[str],
                   nickname: str = "") -> Reply:
    """새 메시지 한 건을 처리한다. 화면 재표시는 이 진입점을 호출하지 않는다."""
    nickname = nickname.strip()
    if state.nickname and nickname != state.nickname:
        state.__init__()
    state.nickname = nickname
    model = experiments.pick_model()  # 실험이 꺼져 있으면 None: 기본 모델 그대로
    turn_id = uuid4().hex
    with track_session(state.usage), use_model(model):
        used = "" if api.is_mock() else current_model()
        try:
            reply = _handle_turn(state, message, job, sidebar_skills)
        except Exception:
            _record_failure(state, turn_id, job, used)
            raise
    reply.turn_id, reply.model = turn_id, used
    if reply.failed:
        _record_failure(state, turn_id, job, used, reply.action)
    return reply


def _record_failure(state: Conversation, turn_id: str, job: str, model: str, action: str = "") -> None:
    """모델 응답이 검사를 통과하지 못한 턴을 실험 기록에 남긴다(모델별 실패 수). 기록 실패는 상담을 막지 않는다."""
    if not experiments.models():
        return
    try:
        experiments.save_feedback(state.usage.session_id, turn_id, job=job, model=model, action=action or None, failed=True)
    except Exception:
        pass


def _handle_turn(state: Conversation, message: str, job: str, sidebar_skills: list[str]) -> Reply:
    if not message.strip():
        return Reply("이어서 말씀해 주세요.")
    try:
        allowed = intake.guard_input(message)
    except intake.ChatIntakeError:
        return Reply("요청의 가드레일 점검을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    if not allowed:
        return Reply("이 요청은 학습 상담 범위에서 처리할 수 없습니다.")
    read_failed = False
    if state.nickname and state.profile is None:
        try:
            state.profile = chat_profiles.load_profile(state.nickname)
        except Exception:
            read_failed = True
    try:
        context = _context(state, job, sidebar_skills)
        turn = intake.interpret(message, context, screened=True)
    except intake.ChatIntakeError:
        return Reply("메시지를 해석하지 못했습니다. 요청을 다시 말씀해 주세요.", action="intake", failed=True)
    if (state.offered_gap and turn.status == "ok" and turn.action == "note"
            and message.strip().rstrip(".!").strip() in {"네", "예", "응", "좋아요", "비교해줘", "비교해 주세요"}):
        turn.action = "gap"
    if turn.status not in {"blocked", "out_of_scope"}:
        state.pending_save = False
        state.offered_gap = False
    reply = _answer(state, turn, job, sidebar_skills)
    reply.action = turn.action if turn.status == "ok" else turn.status
    if turn.status == "ok" and turn.action == "summary" and "상태" in message and not any(
        word in message for word in ("정리", "요약", "만")
    ):
        state.offered_gap = True
        role = JOBS.get(context["job"], "지원 직무")
        reply.text += f"\n\n{role} 공고에서 요구하는 기술과도 비교할까요?"
    if turn.status in {"ok", "unsupported", "clarify_role", "incomplete"}:
        state.history.extend([{"role": "user", "content": message},
                              {"role": "assistant", "content": reply.text}])
        state.history = state.history[-8:]
    if read_failed and state.profile is None:
        reply.text += "\n\n저장된 프로필을 불러오지 못해 이번 상담에서 확인한 정보만 사용했습니다."
    return reply
