"""AWS 없는 시연용 학습 희망·강의 참조 해석. 실제 팀장은 별도 Bedrock 호출을 쓴다."""
from __future__ import annotations

import re
from dataclasses import asdict, replace

from ai import chat, chat_mock
from ai.supervisor import (
    SupervisedTurn, _canonical, _clarify, _course_rank, _save_scopes, _validate_writes, switched_course_reference,
)

_WANT = re.compile(r"(?:배우|배워|공부|학습|익히|익혀|입문|연습|써\s*보|써보|도전|해\s*보).{0,12}"
                   r"(?:싶|계획|예정|희망|려고|려구|관심|할래|볼래)|(?:배우려|익히려)|"
                   r"관심(?:이|을)?\s*(?:있|가)|(?:희망|배우고\s*싶은)\s*(?:기술|분야)")
_NEGATIVE_WISH = re.compile(r"싶지\s*않|싶은\s*(?:게|건|것은)\s*아니|(?:배울|공부할|학습할)\s*생각.{0,4}없|"
                            r"(?:희망|관심).{0,8}(?:제외|빼|삭제|없)")
_REPLACE = re.compile(r"말고|대신(?:에)?|아니라|빼고|제외하고")
_REFERENCE = re.compile(r"그\s*(?:기술|과정|강의|주제|거|걸|쪽|중|것|분야)|이\s*(?:기술|주제|분야)|"
                        r"관련|아까|방금|전에\s*말|앞서|앞에서|위에서|말한|배우고\s*싶다던|거기|그걸로|이걸로")
_GAP_SCOPE = re.compile(r"부족|미달|격차|갭|\bgap\b", re.I)
_ALL = re.compile(r"전부|모두|둘\s*다|두\s*기술|각각|(?:이|그)\s*기술들")
_GENERIC = re.compile(r"^(?:AWS\s+Skill\s+Builder|API|AWS\s+Training|Gap)$", re.I)
_KOREAN_TOPIC = re.compile(r"([가-힣]{2,})(?:\s*(?:관련|입문|기초|심화|기술))?\s*(?:강의|교육\s*과정|강좌)")
_GENERIC_TOPICS = {"관련", "입문", "기초", "심화", "기술", "부족한", "맞는", "관한", "위한", "그중", "추천", "좋은",
                   "연결된", "어떤", "위의", "맞춤", "온라인", "첫번째", "번째", "들을", "맞춘", "배울", "실습",
                   "초급", "중급", "고급", "상급", "어려운", "쉬운", "높은", "낮은", "난이도", "마지막으로"}
_REPORTED = re.compile(r"뜻했|의미했|가리켰|말했|언급했|원했|희망했|요청했|추천했|선택했")
_PERSONAL_USE = re.compile(r"사용|활용|썼|써\s*봤|배포|운영|구축|실습|이용|개발|설계|담당|구현")


def _labels(text: str) -> list[str]:
    """명시적으로 등장한 사전 기술, 영문·따옴표 기술명을 찾는다."""
    # AWS 교육 플랫폼·직무 이름은 강의 검색의 주제와 구분한다.
    text = re.sub(r"AWS\s*(?:Skill\s*Builder|Training)|(?:AI|클라우드)\s*엔지니어", " ", text, flags=re.I)
    names = []
    for part in chat.course_topic_parts(text):
        names.extend(chat.mentioned_skills(part))
        for match in chat_mock._LABEL.finditer(part):
            name = match[0].strip('`"“”‘’\' ')
            if name and not _GENERIC.fullmatch(name) and not chat.mentioned_skills(name):
                names.append("AI" if name.casefold() == "ai" else name)
    return _canonical(names)


def _course_labels(text: str, has_reference: bool) -> list[str]:
    names = _labels(text)
    if names or has_reference or _GAP_SCOPE.search(text) or _course_rank(text) is not None:
        return names
    topic = _KOREAN_TOPIC.search(text)
    if topic and topic[1] not in _GENERIC_TOPICS:
        return [topic[1]]
    return []


def _correct_attribution(turn: chat.Turn, message: str, context: dict) -> None:
    # 기술 이름을 '뜻했다'는 설명은 과거형이지만 그 기술을 사용한 경험은 아니다.
    if _REPORTED.search(message):
        fact_clauses = [clause for clause in chat.owned_clauses(message)
                        if not _REPORTED.search(clause) or _PERSONAL_USE.search(clause)]
        supported = {name for clause in fact_clauses for name in chat_mock._used_skills(clause)}
        turn.add_skills = [name for name in turn.add_skills if name in supported]
    if not re.search(r"정정|바로잡|잘못\s*말|잘못\s*알려", message):
        return
    experiences = context.get("experiences", [])
    known = list(context.get("skills", []))
    experience_uses = {text: {name for clause in chat.owned_clauses(text) for name in chat_mock._used_skills(clause)}
                       for text in experiences}
    known += [name for uses in experience_uses.values() for name in uses]
    for clause in chat._CLAUSES.split(message):
        if not chat.THIRD_PARTY.search(clause) or chat.SELF.search(clause) or not _PERSONAL_USE.search(clause):
            continue
        reassigned = set(chat.target_skills(clause, known)) & set(known) - set(turn.add_skills)
        turn.remove_skills = _canonical(turn.remove_skills + [name for name in known if name in reassigned])
        turn.remove_experiences = list(dict.fromkeys(
            turn.remove_experiences + [text for text, uses in experience_uses.items() if uses & reassigned]))
    turn.experience_skills = [name for name in turn.experience_skills if name not in turn.remove_skills]


def _updated_wishes(message: str, prior: list[str]) -> list[str]:
    wishes = list(prior)
    own = list(chat.owned_clauses(message))
    replacing = _REPLACE.search(message)
    # '말고'는 기존 절 구분기에서도 나뉜다. 철회 대상은 구분하기 전 원문에서 읽는다.
    if replacing and any(_WANT.search(clause) for clause in own):
        before = list(chat.owned_clauses(message[:replacing.start()]))
        if before:
            removed = _labels(before[-1])
            wishes = [name for name in wishes if name.casefold() not in {n.casefold() for n in removed}]
    for clause in own:
        target = _REPLACE.split(clause)[-1]
        names = _labels(target)
        if _NEGATIVE_WISH.search(target):
            wishes = [name for name in wishes if name.casefold() not in {n.casefold() for n in names}]
        elif _WANT.search(target):
            wishes = _canonical(wishes + names)
    return wishes


def _ordered_target(text: str, candidates: list[str]) -> list[str] | None:
    rank = _course_rank(text)
    if rank is None:
        return None
    index = len(candidates) - 1 if rank == -1 else rank - 1
    return candidates[index:index + 1] if 0 <= index < len(candidates) else []


def _pending_answer(message: str, context: dict) -> list[str]:
    if not context.get("pending_course_question") or len(message.split()) > 5:
        return []
    stripped = re.sub(r"(?:으로|로|이요|요|입니다|할게요|부탁해요)[.!?\s]*$", "", message).strip(" .!?")
    selected = _labels(stripped)
    # 새 사용 경험이나 프로필 요청의 일부에 이름이 나왔다고 해서 앞선 과정을 재개하지 않는다.
    remainder = chat._alias_pattern().sub("", stripped)
    for name in selected:
        remainder = re.sub(re.escape(name), "", remainder, flags=re.I)
    return selected if re.fullmatch(r"[\s,]*(?:(?:와|과|랑|하고|및|둘\s*다|만)[\s,]*)*", remainder) else []


def _targets(message: str, context: dict, wishes: list[str], pending: list[str]) -> tuple[list[str], bool]:
    if pending:
        return pending, False
    clauses = [clause for clause in chat.owned_clauses(message) if chat.COURSE.search(clause)]
    scope = _REPLACE.split(clauses[-1] if clauses else message)[-1]
    order = _ordered_target(scope, context.get("last_gap_skills", []))
    if order is not None:
        return order, not bool(order)
    has_reference = bool(_REFERENCE.search(message))
    names = _course_labels(scope, has_reference)
    if names:
        return names, False
    if _GAP_SCOPE.search(scope):
        return [], False
    if has_reference:
        candidates = wishes
        if not candidates:
            candidates = context.get("last_gap_skills", [])
        if not candidates:
            previous = next((m["content"] for m in reversed(context.get("history", [])) if m["role"] == "user"), "")
            candidates = _canonical([name for part in chat.owned_clauses(previous) for name in _labels(part)])
        if len(candidates) == 1 or (candidates and _ALL.search(scope)):
            return list(candidates), False
        return [], True
    return wishes, False


def mock_plan(message: str, context: dict) -> SupervisedTurn:
    turn = chat.interpret(message, context, screened=True)
    prior = list(context.get("wishes", []))
    if turn.status != "ok":
        return SupervisedTurn(turn, prior, decisions=["continue"])
    _correct_attribution(turn, message, context)
    if turn.save:
        scopes = _save_scopes(message, context)
        if len(scopes) > 1:
            turn.save_job, turn.save_skills, turn.save_experiences = False, [], []
            for scope in scopes:
                selection = replace(turn, save_job=False, save_skills=[], save_experiences=[])
                chat_mock._save_targets(selection, scope + " 저장해줘", context)
                turn.save_job |= selection.save_job
                turn.save_skills = _canonical(turn.save_skills + selection.save_skills)
                turn.save_experiences = list(dict.fromkeys(turn.save_experiences + selection.save_experiences))
        try:
            _validate_writes(asdict(turn), context, scopes)
        except ValueError:
            return SupervisedTurn(chat.Turn(), prior, question="프로필에 반영할 항목을 지정해 주세요.", decisions=["clarify"])
    wishes = _updated_wishes(message, prior)
    pending = _pending_answer(message, context)
    if turn.action == "note":
        if pending or (wishes and _REFERENCE.search(message) and re.search(r"추천\s*해\s*(?:줘|주세요)", message)):
            turn.action = "courses"
        elif context.get("offered_gap") and re.fullmatch(r"(?:네|예|응|좋아(?:요)?|그래(?:요)?)[.!\s]*", message.strip()):
            turn.action = "gap"
    if turn.action != "courses":
        return SupervisedTurn(turn, wishes, decisions=["continue"])
    reference = switched_course_reference(message, context, turn)
    if reference:
        scope, targets, _ = reference
        unclear = scope == "clarify"
    else:
        targets, unclear = _targets(message, context, wishes, pending)
    if not reference and turn.course_difficulty and not pending:
        named = chat.explicit_course_skills(message, [*wishes, *context.get("last_course_skills", [])])
        if named:
            targets, unclear = named, False
        elif context.get("last_course_skills") and not chat.requests_gap_courses(message) and _course_rank(message) is None:
            targets, unclear = list(context["last_course_skills"]), False
    if unclear:
        stored = chat.Turn(job=turn.job, remove_skills=_canonical([*context.get("skills", []), *turn.remove_skills]),
                           remove_experiences=list(context.get("experiences", [])))
        return _clarify(context, "course_target", ["clarify"], courses=True, message=message, proposed=stored)
    return SupervisedTurn(turn, wishes, targets, decisions=["continue"])
