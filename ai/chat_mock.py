"""AWS 없이 시연할 때 사용하는 보수적인 한국어 상담 예시 해석기."""
from __future__ import annotations

import re

from ai.chat import (
    COURSE, FRAGMENT, NEGATION, SELF, THIRD_PARTY, WISH, Turn, active_experiences, mentioned_skills,
    owned_clauses, request_clauses, save_scope, skill_source, target_skills,
)
from backend.jobs import JOBS

_DONE = re.compile(r"했|했어|했다|했고|했습니다|썼|써\s*(?:봤|왔)|사용해\s*(?:봤|왔)|"
                   r"해\s*(?:봤|왔|본)|맡았|만들었|다뤘|구축한|개발한|배포한|경험이?\s*있|"
                   r"(?:사용|운영|개발|배포|관리|구현|담당|구축|설계|작업|다룹|다룰\s*수)\s*(?:해요|합니다|중|있)")
_WORK = re.compile(r"개발|배포|구축|구현|설계|운영|자동화|수집|모니터링|관리|분석|"
                   r"구성|만들|작성|담당|프로젝트|실습|수강|테스트|마이그레이션")
_DEFER = re.compile(r"나중|보류|말아|말자|말고|하지\s*마|하지\s*말|안\s*(?:해|할)|필요\s*없|됐어")

_GAP = re.compile(r"부족|보완|우선순위|우선\s*순위|격차|[Gg][Aa][Pp]|"
                  r"(?:무엇|뭐|뭘|어떤\s*기술).{0,16}(?:공부|준비|배워)|어디부터.{0,10}(?:시작|공부)")
_ASK = re.compile(r"추천\s*(?:해(?:\s*(?:줘|주세요|줄래)|$)|부탁|좀(?=\s|$)|바라)|"
                  r"알려|(?:비교|분석)해|찾아\s*(?:줘|주|봐)|보여\s*(?:줘|주)|봐\s*줘|봐주|보고\s*싶|"
                  r"뭐|무엇|뭘|어디부터|어떤.{0,15}(?:들|좋|있|할)|있을까|궁금|\?|"
                  r"(?:공부|준비|들으?)(?:해?야|을까|할까)|부탁")
_SUMMARY = re.compile(r"정리|요약|내\s*(?:상태|현황)|지금까지.{0,12}(?:경험|기술)|보유\s*기술.{0,8}(?:보여|알려)")
_ROLE = re.compile(r"(?:[A-Za-z가-힣]+\s*)?(?:엔지니어|개발자|기획자|분석가|과학자|디자이너|연구원)|"
                   r"AI\s*인프라|인공지능\s*인프라|(?:클라우드|AI|백엔드|데이터)\s*직무|"
                   r"백엔드|프론트엔드", re.I)
_GOAL = re.compile(r"기준|준비|목표|지망|취업|이직|전향|전직|지원|되고\s*싶|희망|관점|상담|커리어")
def _job(text: str) -> tuple[str, str | None]:
    mentions = list(_ROLE.finditer(text))
    for index, match in enumerate(mentions):
        next_start = mentions[index + 1].start() if index + 1 < len(mentions) else len(text)
        before, after = text[:match.start()], text[match.end():next_start]
        subject = re.split(r"[.!?,]|(?:지만|는데|했고)\s+", before)[-1]
        if THIRD_PARTY.search(subject) and not SELF.search(subject):
            continue
        # 다음 절이나 다른 직무의 목표 표현을 현재 명사에 붙이지 않는다.
        tail = re.split(r"[,.;!?]|(?:였|였고|였지만|와\s*협업|와\s*함께)|\s+그리고\s+", after)[0]
        if re.match(r"\s*(?:가|이|는|은|를|을)?\s*(?:아니|말고)", after):
            continue
        if re.search(r"(?:준비|목표|희망).{0,10}(?:였|이었|했지만|었지만|하다가)", tail[:24]):
            continue
        if not (_GOAL.search(tail[:22]) or re.search(r"(?:목표|희망)\s*(?:직무)?(?:는|은)?\s*$", before)
                or match[0].strip() == text.strip()):
            continue
        role = re.sub(r"\s+", "", match[0]).casefold()
        for code, label in JOBS.items():
            if role == label.replace(" ", "").casefold() or role == label.split()[0].casefold() + "직무":
                return "ok", code
        if role in ("ai인프라", "인공지능인프라"):
            return "clarify_role", None
        return "unsupported", None
    for code, label in JOBS.items():
        if re.search(re.escape(label.split()[0]) + r"\s*(?:기준|관점|쪽으로)", text):
            return "ok", code
    return "ok", None


def _deferred(text: str, intent: re.Pattern) -> bool:
    for marker in intent.finditer(text):
        before = text[max(0, marker.start() - 10):marker.start()]
        after = text[marker.end():marker.end() + 18]
        if re.search(r"(?:나중에|아직은?\s*안)\s*$", before) or _DEFER.match(
            re.sub(r"^(?:\s|은|는|을|를|도|좀|추천|해주지|해주지는|찾지|해|주지)*", "", after)
        ):
            return True
    return False


def _action(text: str) -> str:
    action = "note"
    for clause in request_clauses(text):
        if _SUMMARY.search(clause):
            action = "summary"
        if _ASK.search(clause):
            if _GAP.search(clause) and not _deferred(clause, _GAP):
                action = "gap"
            if COURSE.search(clause) and not _deferred(clause, COURSE) and not _deferred(clause, re.compile("추천")):
                action = "courses"
    return action


def _used_skills(clause: str) -> list[str]:
    return mentioned_skills(skill_source(clause)) if _DONE.search(clause) else []


def _facts(turn: Turn, message: str, context: dict) -> None:
    for clause in owned_clauses(message):
        if WISH.search(clause) or _deferred(clause, COURSE):
            continue
        if NEGATION.search(clause):
            if re.search(r"(?:저장|기억|추천|분석|수강)\s*(?:은|는)?\s*(?:하지|안)", clause):
                continue
            for skill in mentioned_skills(clause):
                had_skill = skill in context.get("skills", [])
                for fact in context.get("experiences", []):
                    if any(skill in _used_skills(part) for part in owned_clauses(fact)):
                        had_skill = True
                        if fact not in turn.remove_experiences:
                            turn.remove_experiences.append(fact)
                if had_skill and skill not in turn.remove_skills:
                    turn.remove_skills.append(skill)
            continue
        if not _DONE.search(clause):
            continue
        turn.add_skills.extend(s for s in _used_skills(clause) if s not in turn.add_skills)
        if _WORK.search(clause):
            turn.add_experiences.append(clause)


def _save_targets(turn: Turn, message: str, context: dict) -> None:
    scoped = save_scope(message, context)
    if scoped is None:
        return
    turn.save = True
    last = context.get("last_changes") or {}
    named = target_skills(scoped, context.get("skills", []))
    experience_only = bool(re.search(r"경험|작업|프로젝트|맡은\s*일", scoped))
    turn.save_job = bool(re.search(r"(?:목표|직무)(?:도|를|만|는|\s|$)", scoped))
    for group in ("skills", "experiences"):
        recent = (getattr(turn, f"add_{group}") + getattr(turn, f"remove_{group}")
                  or last.get(f"add_{group}", []) + last.get(f"remove_{group}", []))
        derived = turn.experience_skills if group == "skills" else []
        available = list(dict.fromkeys([*context.get(group, []), *derived, *recent]))
        if group == "skills" and experience_only:
            continue
        if group == "skills" and named:
            selected = [s for s in named if s in available]
        elif group == "experiences" and experience_only and named:
            selected = [e for e in available if set(named) <= set(mentioned_skills(e))]
            operations = _WORK.findall(scoped)
            if operations:
                selected = [e for e in selected if any(op in e for op in operations)]
            if len(selected) > 1 and not re.search(r"전부|모두|다\s", scoped):
                selected = []
        elif re.search(r"전부|모두|전체", scoped) and not named:
            selected = available
        elif not named and not turn.save_job and (experience_only or re.search(
            r"이\s*(?:내용|기술)|방금|정정한|그거|이거", scoped
        ) or getattr(turn, f"add_{group}") or getattr(turn, f"remove_{group}")):
            selected = recent
        else:
            selected = []
        setattr(turn, f"save_{group}", selected)


def mock_turn(message: str, context: dict) -> Turn:
    if FRAGMENT.search(message):
        return Turn(status="incomplete")
    status, job = _job(message)
    if status != "ok":
        return Turn(status=status)
    if re.search(r"날씨|점심\s*메뉴|운세|로또|주식\s*추천|저녁\s*메뉴", message):
        return Turn(status="out_of_scope")
    turn = Turn(action=_action(message), job=job)
    history = context.get("history", [])
    user = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
    assistant = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), "")
    role_question = re.search(r"기준|관점|뜻인|원하|의미|맞", assistant)
    if (context.get("needs_role") or _job(user)[0] == "clarify_role") and role_question and re.match(
        r"(?:네|예|응|맞아|좋아|클라우드|AI)", message, re.I
    ):
        # 답에 직무 이름이 있으면 그것, 없으면 확인 질문이 직무 하나만 물었을 때 그 직무(둘을 물었으면 고르지 않는다)
        named = [code for code, label in JOBS.items() if re.search(re.escape(label.split()[0]), message, re.I)]
        asked = [code for code, label in JOBS.items() if label in assistant]
        agreed = named[0] if len(named) == 1 else (asked[0] if len(asked) == 1 else job)
        if agreed:
            turn.job = agreed
            if turn.action == "note":
                turn.action = _action(user)
    if turn.job and context.get("needs_role") and turn.action == "note" and re.search(r"추천해\s*(?:줘|주세요)", message):
        turn.action = _action(user)
    if (turn.action == "note" and re.search(r"(?:그럼|그중|이제).{0,12}추천해", message)
            and _GAP.search(user) and not _DEFER.search(message)):
        turn.action = "courses"
    if FRAGMENT.search(user) and _DONE.search(message):
        fragment = user.rstrip(".… ").strip()
        if fragment and mentioned_skills(fragment) and not WISH.search(fragment):
            message = fragment + " " + message
    _facts(turn, message, context)
    confirmed = Turn()
    for experience in active_experiences(context, turn.add_experiences, turn.remove_experiences):
        _facts(confirmed, experience, {})
    excluded = set(context.get("excluded_skills", [])) | set(turn.remove_skills)
    turn.experience_skills = [name for name in confirmed.add_skills if name not in excluded]
    _save_targets(turn, message, context)
    return turn
