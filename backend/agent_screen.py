"""화면 상담을 구조 A(상담 에이전트, ai/agent.py)로 처리하는 길. CHAT_ARCHITECTURE=agent 일 때만 쓴다.

backend/chat.handle_message 가 부른다. 화면(app/)은 고치지 않는다: 돌려주는 값이 구조 0 과 같은 Reply 이기 때문이다.

- 보유 기술: 저장된 프로필과 사이드바 기술을 코드가 합쳐 A 의 get_gap 입력에 더한다(ai.agent.chat 의 base_skills). 프롬프트는 그대로
- 입력 검사: A 안의 ai.guardrail.check_input 결과를 구조 0 과 같은 문구·action 으로 바꾼다(막힘 blocked, 검사 실패 intake)
- 과정 카드: A 가 받은 과정 원본을 구조 0 의 카드 검사(backend.chat._verified_course_cards)로 그대로 옮긴다
- 수준 진단 문항: A 가 get_gap 에 넘긴 기술을 코드로 표준화해 assessment_skills 로 넘긴다(구조 0 처럼 말한 기술의 문항이 열린다)
- 구조 0 이 처리하는 턴
  · 클라우드 엔지니어가 아닌 턴(A 는 클라우드만)
  · 직무 바꾸기·저장 요청: 낱말 검사(SCREEN_CUES)에 걸린 턴만 구조 0 의 해석(모델 호출 1번)을 부르고, 해석이 직무 변경·저장·
    지원하지 않는 직무·직무 확인이면 그 턴은 구조 0 이 처리한다(해석은 한 번만, backend.chat._handle_turn 의 agent_gate).
    해석이 그 밖이거나 실패하면 A 가 처리한다
  · A 실행 중 예외가 난 턴. 가드레일 차단은 예외가 아니라 대신 처리하지 않는다. 예외는 종류만 로그에 남긴다(원문 없음)
- 저장 완료 표현: A 에는 저장 도구가 없어, A 의 답에 "저장했어요" 같은 표현이 있으면 저장되지 않았다는 고정 안내를 붙인다(SAVE_CLAIM)
- 이전 대화: A 와 구조 0 이 처리한 턴을 모두 Conversation.agent_history 에 쌓아 A 에 넘긴다(막힘·범위 밖·실패 턴 제외)
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from backend import chat_profiles
from backend.jobs import JOBS
from pipeline.aliases import normalize_skills

if TYPE_CHECKING:
    from ai.chat import Turn
    from backend.chat import Conversation, Reply

logger = logging.getLogger(__name__)
HISTORY_TURNS = 6  # A 에 넘기는 이전 대화(질문·답 쌍) 수. 대화가 길어져도 입력 토큰이 끝없이 늘지 않게
# 직무 바꾸기·저장 요청일 수 있는 낱말. 걸린 턴만 구조 0 의 해석을 부른다(걸리지 않으면 모델 호출이 늘지 않는다)
SCREEN_CUES = re.compile("|".join([*(re.escape(name) for name in JOBS.values()),
                                   r"직무", r"목표", r"(?<![a-z])AI(?![a-z])", r"인공지능", r"저장", r"프로필", r"기억해"]),
                         re.I)  # "AI" 는 영어 낱말 속(Airflow 등)에서는 걸지 않는다
UNREMEMBERED = {"blocked", "out_of_scope"}
# A 에는 저장 도구가 없다. A 의 답에 저장을 마쳤다·해 주겠다는 표현이 있으면 코드가 끝에 고정 안내를 붙인다(프롬프트는 그대로).
# "저장하는 방법", "저장했던 경험", "저장되었는지", "반영해 추천", "기억해 두면" 같은 설명·조건 문장은 걸지 않는다
_KEPT = r"해\s*(?:두었|뒀|놓았|드렸|드릴게|드리겠|둘게)"  # 해 두었다·해 드렸다·해 드릴게요·해 드리겠습니다·해 둘게요
SAVE_CLAIM = re.compile(
    r"(?:저장(?:했|되었|됐|" + _KEPT + r")"
    r"|저장이?\s*완료(?:되었|됐|했)"
    r"|(?:기억|반영)" + _KEPT
    + r"|프로필에\s*(?:추가|반영|등록)(?:했|되었|됐|" + _KEPT + r"))"
    r"(?!던|다면|는지|을\s*때|으면)")
NOT_SAVED_NOTE = ("이 대화에서는 프로필에 저장되지 않았어요. 저장하려면 'Docker로 API 배포한 경험을 저장해 줘'처럼 "
                  "한 문장으로 말해 주세요.")


def base_skills(state: Conversation, sidebar_skills: list[str]) -> list[str]:
    """저장된 프로필 기술 + 사이드바 기술(중복 없이). 프로필을 못 읽으면 사이드바만(구조 0 과 같다)."""
    if state.session is not None and state.profile is None:
        try:
            state.profile = chat_profiles.load_profile(state.session)
        except Exception:
            pass
    names = [*(state.profile or {}).get("skills", []), *sidebar_skills]
    return list(dict.fromkeys(str(n).strip() for n in names if str(n).strip()))


def wants_screen(turn: Turn, context: dict) -> bool:
    """구조 0 의 해석으로 보아 구조 0 이 처리할 턴인가: 직무 바꾸기, 저장, 지원하지 않는 직무, 직무 확인."""
    if turn.status in {"unsupported", "clarify_role"}:
        return True
    return turn.status == "ok" and (turn.save or bool(turn.job and turn.job != context["job"]))


def _action(result: dict, courses: list[dict]) -> str:
    """화면의 이동 버튼과 대화 기록 저장 규칙이 읽는 요청 종류. 부른 도구로 정한다."""
    if result.get("scope") == "out":
        return "out_of_scope"
    if courses:
        return "courses"
    called = {c["name"] for c in result["tool_calls"] if c.get("ok")}
    if "get_gap" in called:
        return "gap"
    return "demand" if "get_skill_demand" in called else "note"


def said_skills(result: dict) -> list[str]:
    """A 가 이번 턴에 get_gap 에 넘긴 기술(사용자가 말한 것)을 별칭 사전으로 표준화. 사전에 없는 표기는 뺀다."""
    raws = [str(s) for c in result["tool_calls"] if c["name"] == "get_gap" and c.get("ok")
            for s in (c.get("input") or {}).get("user_skills", [])]
    known, _ = normalize_skills(raws)
    return list(dict.fromkeys(known))


def with_save_note(answer: str) -> str:
    """A 의 답이 저장을 마쳤다고 말하면 저장되지 않았다는 고정 안내를 끝에 붙인다."""
    return f"{answer}\n\n{NOT_SAVED_NOTE}" if SAVE_CLAIM.search(answer) else answer


def _remember(state: Conversation, message: str, text: str) -> None:
    state.agent_history = [*state.agent_history, {"role": "user", "content": message},
                           {"role": "assistant", "content": text}][-HISTORY_TURNS * 2:]


def remember_screen_turn(state: Conversation, message: str, reply: Reply) -> None:
    """구조 0 이 처리한 턴도 A 의 이전 대화에 넣는다. 막힘·범위 밖·실패 턴과 빈 메시지는 넣지 않는다."""
    if reply is None or reply.failed or reply.action in UNREMEMBERED or not message.strip():
        return
    _remember(state, message, reply.text)


def reply_for(state: Conversation, message: str, job: str, sidebar_skills: list[str],
              sidebar_levels: dict[str, int] | None = None) -> tuple[Reply | None, bool]:
    """(Reply, 구조 A 가 답했는가). Reply 가 None 이면 이 턴은 구조 0 이 처음부터 처리한다."""
    from ai import agent
    from backend import chat

    if not message.strip():
        return None, False
    skills = base_skills(state, sidebar_skills)
    target = state.job or (state.profile or {}).get("job") or job
    if target != agent.JOB:
        return None, False
    if SCREEN_CUES.search(message):
        # 구조 0 이 입력 검사 → 해석까지 한다. 구조 0 이 처리할 턴이면 끝까지 처리한 답, 아니면 None
        try:
            screened = chat._handle_turn(state, message, job, sidebar_skills, sidebar_levels, agent_gate=wants_screen)
        except Exception as e:  # 판정 단계의 예기치 않은 오류도 A 로 넘긴다(종류만 로그)
            logger.warning("구조 0 판정 실패(%s). 이 턴은 구조 A 로 처리합니다", type(e).__name__)
            screened = None
        if screened is not None:
            return screened, False  # 직무 바꾸기·저장 등, 또는 입력 검사에서 막힘
    try:
        result = agent.chat(message, state.agent_history[-HISTORY_TURNS * 2:], base_skills=skills)
        if result["guard"] == "blocked":
            return chat.Reply(chat.BLOCKED_TEXT, action="blocked"), True
        if result["guard"] == "check_failed":
            return chat.Reply(chat.CHECK_FAILED_TEXT, action="intake", failed=True), True
        courses = [card for row in result["course_rows"]
                   for card in chat._verified_course_cards(row["gap_skill"], row["courses"])]
        action = _action(result, courses)
        assessed = said_skills(result)
        answer = with_save_note(result["answer"])
    except Exception as e:  # 시연 중 화면이 깨지지 않게 이 턴만 구조 0 으로
        logger.warning("상담 구조 A 처리 실패(%s). 이 턴은 구조 0 으로 처리합니다", type(e).__name__)
        return None, False
    if action not in UNREMEMBERED:
        _remember(state, message, answer)
    reply = chat.Reply(answer, courses=courses, action=action, assessment_skills=assessed)
    if assessed:
        reply.assessment_job = agent.JOB
    return reply, True
