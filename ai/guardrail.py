"""가드레일 (#24). 화면 상담과 상담 에이전트(ai.agent)의 입력·생성 답변을 검사한다.

  check_input(text)  -> GuardResult   사용자 입력. 결과는 통과 / 안전상 차단 / 검사 실패(is_check_failure) 셋
  check_output(text) -> GuardResult   화면 답변 작성기(ai.chat_reply)와 상담 에이전트가 생성한 문장.

팀 결정 (11/10, 11/18 #24)
- 성별·학력·인종(국적)·지역을 근거로 능력이나 채용 가능성을 판단하거나 차별하는 요청은 막는다. 나이는 막지 않는다.
- 속성을 언급만 한 정상 질문은 통과해야 한다("충청 지역에서 취업 준비 중이에요", "비전공자인데").
  그래서 속성 낱말 하나로 막지 않고, 같은 문장에 "판단·차별 요청" 표현이 함께 있을 때만 막는다.
- 프롬프트 공격(이전 지시 무시, 시스템 프롬프트 요구, 제한 없는 AI 역할극)도 막는다(범주 "공격").

두 단계 (둘 다 쓰고, 조합은 테스트 표로 정한다)
1. 코드 검사: 아래 낱말표. **질문에만** 돈다. 비용 0, 가짜 모드에서도 돈다.
   답에는 돌리지 않는다(11/19): 답은 우리 모델이 도구 결과로 만들고, 기술 답에 흔한 "지역·유리·제한 없이"로
   낱말 방식 오탐이 세 번 났다. 답 검사는 GUARDRAIL_OUTPUT=1 일 때 Guardrails 만 한다.
2. Bedrock Guardrails(ApplyGuardrail): 실제 모드에서 입력은 늘, 답은 GUARDRAIL_OUTPUT=1 일 때만. 가짜 모드는 부르지 않는다.
   ID·버전은 같은 묶음에서만 고른다(guardrail_config): GUARDRAIL_ID 가 있으면 GUARDRAIL_ID + GUARDRAIL_VERSION,
   없으면 BEDROCK_GUARDRAIL_ID + BEDROCK_GUARDRAIL_VERSION. 버전 기본값은 없다.
   검사를 끝내지 못하면 막는다(fail-closed, CLAUDE.md "검사에 실패하면 그 요청은 종료"): ID·버전 누락(AWS 를 부르지 않음),
   호출 예외(권한, SSO 만료, 호출 제한), action 누락·알 수 없음, INTERVENED 인데 판정을 읽을 수 없음.
   이때 결과 범주는 FAILED 다. 화면은 ChatIntakeError, 상담 에이전트는 UNAVAILABLE_MESSAGE 로 안내한다.
   이메일·전화번호 가리기(ANONYMIZED)만 된 개입도 막는다: 화면 계약(bool)이 가려진 문장을 넘기지 못해
   통과시키면 원문이 다음 모델로 가기 때문이다.
   GUARDRAIL_TOPIC_BLOCK(기본 1): 1 이면 차단 주제와 필터(프롬프트 공격·혐오·모욕) 모두로 막는다. 0 이면 차단 주제는 기록만 한다.
   시연 설정(11/19)은 버전 2 + 1: 버전 2 주제 정의에서 학력 언급 질문 오탐이 없어졌다.
   버전 1 은 차단 주제가 "비전공자인데…" 질문을 막아(오탐) 0 으로 둔다.
   막는 힘의 주력은 Guardrails 다: 보지 않은 문항에서 코드 검사는 막을 질문을 하나도 막지 못했다.
막히는 조건: 코드 검사가 막거나, Guardrails 가 (설정에 따라 쓰는) 정책으로 막으면 막는다.

기록: 막힘 여부, 단계(code/guardrails), 범주, 규칙 이름만 로그에 남긴다. 입력·답 원문은 남기지 않는다(CLAUDE.md 보안).
평가: data/eval/guardrail_cases.json(개선용 33문항, 공개 범위 밖)으로 오탐률·미탐률을 잰다(python -m ai.eval.guardrail_eval).
"""
from __future__ import annotations

import logging
import os
import re
from typing import NamedTuple, Optional

from ai import bedrock
from ai.bedrock import is_mock

BLOCK_MESSAGE = (
    "이 서비스는 성별, 학력, 출신 지역, 인종이나 국적 같은 조건으로 직무나 채용 가능성을 판단하지 않습니다. "
    "해 본 기술과 관심 있는 직무를 알려 주시면, 필요한 역량과 AWS 교육과정을 안내해 드릴게요."
)
OUTPUT_BLOCK_MESSAGE = "답을 정리하지 못했습니다. 질문을 바꿔 다시 물어봐 주세요."
UNAVAILABLE_MESSAGE = "지금은 안전 검사를 할 수 없어 답변을 드릴 수 없어요. 잠시 후 다시 시도해 주세요."
PII_MESSAGE = "이메일이나 전화번호 같은 개인정보는 빼고 다시 물어봐 주세요."
FAILED = "실패"  # GuardResult.category: 검사를 끝내지 못해 막음(설정 누락·호출 실패·해석 불가). 안전상 차단과 구분

CATEGORIES = ("성별", "학력", "인종", "지역", "공격")

# 속성 낱말. 하나만 있으면 막지 않는다(언급만 한 정상 질문). 나이는 넣지 않는다(팀 결정)
ATTRIBUTES = {
    "성별": r"여자|남자|여성|남성|성별|여직원|남직원",
    "학력": r"지방대|고졸|대졸|명문대|학벌|학력|전문대|서울대|인서울|스카이|SKY|출신\s*학교",
    "인종": r"외국인|한국인|흑인|백인|동남아|인종|국적|조선족|아시아인",
    "지역": r"전라도|경상도|충청도|강원도|제주도|호남|영남|지방\s*출신|지방|수도권|출신\s*지역|출신지|지역",
}
# 판단·차별 요청 표현. 속성 낱말과 같은 문장에 있을 때만 막는다. 입력 검사에서만 쓴다
# 11/19 리뷰: "유리·불리·안 맞·더 맞·포기·떨어지·소용 없·의미 없·피하는"은 상담·기술 문장에도 흔해 뺐다
# ("남자인데 포기하지 않고 준비 중", "지방에 살아서 오프라인 수업은 안 맞아요", "다중 AZ 구성이 지역 장애에 유리").
# 판단을 분명히 요구하는 표현만 남긴다. 그만큼 놓치는 요청은 Bedrock Guardrails 필터가 맡는다
# 11/19 리뷰: "성향" 단독은 "지방에 살아서 제 성향상 원격 수업이 맞아요" 같은 자기 소개를 막아,
# 사람·출신 집단의 성향을 말하는 형태("○○ 사람이 개발자 성향에 더 맞나요")만 남겼다
# 11/19 기술 답 정상 문장 세트: "따라 … 다르게"는 "리전에 따라 요금이 다르게"를 막아 뺐다(B4 는 "합격 확률"로 막힌다)
# 11/19 리뷰: "걸러·거르·숨기" 단독은 "CloudWatch 로그를 지역별로 걸러서", "S3 버킷 지역 정보를 숨기고",
# "국적 필드를 걸러내는" 같은 기술 질문을 막아, 사람·집단을 걸러내거나 숨기는 형태만 남겼다
_PEOPLE = r"(지원자|사람|여성|남성|여자|남자|출신|외국인|흑인|백인|고졸|지방대)"
JUDGMENT = (
    r"안\s*뽑|뽑지\s*않|만\s*뽑|뽑아\s*주지|(사람|출신)\S{0,3}\s.{0,12}성향|실력이\s*떨어|"
    r"피해야|피하라|기준으로\s*추천|합격\s*확률|"
    + _PEOPLE + r"\S{0,4}\s*.{0,15}(걸러|거르|숨기)"
)
# 프롬프트 공격. 낱말 하나가 아니라 분명한 지시 문장 형태만 막는다(11/19: "S3는 용량 제한 없이", "Dan 이라고 합니다" 오탐).
# (규칙 이름, 패턴, 대소문자 무시 여부). DAN 은 대소문자를 구분한다(사람 이름 Dan)
ATTACKS = (
    ("지시 무시", r"(이전|앞의|위의|기존)\s*(지시|명령|규칙|지침)\S*\s*(은|는|을|를)?\s*(모두\s*)?"
                  r"(무시\s*(하고|해|하라|해라|하세요)|잊\s*(어|고|으라|어라|으세요))", True),
    ("시스템 프롬프트",
     r"(시스템\s*프롬프트|system\s*prompt|숨겨진\s*(설정|지침|프롬프트)|내부\s*(지침|프롬프트))\S*\s*.{0,10}?"
     r"(알려|보여|출력|공개|말해|reveal|show|print|tell)", True),
    ("제한 해제",
     r"(너는|넌|당신은|you\s+are)\s*(이제\s*|now\s+)?(제한|규칙|필터|검열)\s*(이\s*)?(없는|풀린)"
     r"|(제한|규칙|필터|검열)\s*(이\s*)?없이\s*(답|대답|말|응답)"
     r"|규칙\s*(은\s*)?신경\s*쓰지\s*말", True),
    ("탈옥", r"탈옥\s*(모드|해|시켜|프롬프트)|jailbreak\s*(mode|prompt)", True),
    ("DAN", r"\bDAN\s*(모드|mode|처럼|으로\s*(행동|답|대답))", False),
    ("영어 지시 무시", r"ignore\s+(all\s+|the\s+)?(previous|prior|above)\s+instructions", True),
)
_SENTENCE = re.compile(r"[.?!。\n]+")

logger = logging.getLogger(__name__)


class GuardResult(NamedTuple):
    blocked: bool
    source: Optional[str] = None    # "code" 또는 "guardrails"
    category: Optional[str] = None  # CATEGORIES 중 하나. Guardrails 는 정책 이름
    rule: Optional[str] = None      # 걸린 규칙 이름(원문 아님)


PASS = GuardResult(False)


def code_check(text: str) -> GuardResult:
    """질문 낱말표 검사. 공격 지시 문장은 어디에 있어도, 차별 판단은 속성 낱말과 판단 표현이 같은 문장에 있을 때만 막는다.

    답에는 쓰지 않는다(_check). 답의 검사는 GUARDRAIL_OUTPUT=1 일 때 Guardrails 가 맡는다.
    """
    for name, pattern, ignore_case in ATTACKS:
        if re.search(pattern, text, flags=re.IGNORECASE if ignore_case else 0):
            return GuardResult(True, "code", "공격", name)
    for sentence in _SENTENCE.split(text):
        if not re.search(JUDGMENT, sentence):
            continue
        for category, pattern in ATTRIBUTES.items():
            if re.search(pattern, sentence, flags=re.IGNORECASE):
                return GuardResult(True, "code", category, "속성+판단")
    return PASS


class GuardrailCallError(Exception):
    """Guardrails 검사를 끝내지 못함(설정 누락, 호출 예외, 해석할 수 없는 응답). 규칙 이름만 담는다(원문·설정값 없음)."""


def guardrail_config() -> tuple[str, str]:
    """(Guardrail ID, 버전). 같은 설정 묶음에서만 고른다. 다른 묶음의 버전으로 채우지 않는다.

    GUARDRAIL_ID 가 있으면 GUARDRAIL_ID + GUARDRAIL_VERSION, 없으면 BEDROCK_GUARDRAIL_ID + BEDROCK_GUARDRAIL_VERSION
    (학습 상담 화면이 쓰던 예전 이름). 버전 기본값은 없다(DRAFT 로 대신하지 않음).
    """
    gid = os.getenv("GUARDRAIL_ID", "").strip()
    if gid:
        return gid, os.getenv("GUARDRAIL_VERSION", "").strip()
    return os.getenv("BEDROCK_GUARDRAIL_ID", "").strip(), os.getenv("BEDROCK_GUARDRAIL_VERSION", "").strip()


def _guardrails_enabled(source: str) -> bool:
    """Guardrails 를 부를 차례인가. 가짜 모드는 늘 아니다. 실제 모드 입력은 늘, 답은 GUARDRAIL_OUTPUT=1 일 때만.

    설정이 있는지는 보지 않는다: 실제 모드에서 설정이 없으면 bedrock_assess 가 검사 실패로 돌려준다.
    """
    if is_mock():
        return False
    return source == "INPUT" or os.getenv("GUARDRAIL_OUTPUT", "0") == "1"


def topic_block_enabled() -> bool:
    """Guardrails 차단 주제 결과로도 막을지. 기본은 켬(.env 를 빠뜨려도 막는 쪽으로). GUARDRAIL_TOPIC_BLOCK=0 이면 기록만.

    11/19 버전 2(주제 정의 수정본)는 개선용·보지 않은 문항 모두 오탐 0 이라 켜 둔다.
    버전 1 은 차단 주제가 학력을 언급만 한 정상 질문을 막아(오탐) 0 으로 두고 필터로만 막는다.
    """
    return os.getenv("GUARDRAIL_TOPIC_BLOCK", "1").strip() != "0"


def bedrock_assess(text: str, source: str) -> Optional[list[tuple[str, str]]]:
    """ApplyGuardrail 을 한 번 부르고 판정 목록 [(범주, 이름)] 을 돌려준다. 원문은 꺼내지 않는다.

    - None: 부를 차례가 아님(가짜 모드, 답 검사가 꺼짐)
    - []: action NONE(개입 없음)
    - 목록: action GUARDRAIL_INTERVENED 의 판정. 범주는 "주제"(차단 주제), "공격"(프롬프트 공격 필터),
      "콘텐츠"(혐오·모욕 등), "낱말"(금지 낱말), "개인정보"(BLOCKED 된 개인정보), "가리기"(ANONYMIZED 만 된 개인정보)
    - GuardrailCallError: 설정 누락(ID·버전), 호출 예외, action 누락·알 수 없음, INTERVENED 인데 판정을 읽을 수 없음
    """
    if not _guardrails_enabled(source):
        return None
    gid, version = guardrail_config()
    if not gid:
        raise GuardrailCallError("설정 누락")
    if not version:
        raise GuardrailCallError("버전 누락")
    try:
        resp = bedrock._client().apply_guardrail(
            guardrailIdentifier=gid, guardrailVersion=version, source=source,
            content=[{"text": {"text": text}}],
        )
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 막는 쪽으로(부른 쪽이 처리)
        raise GuardrailCallError(type(e).__name__) from None
    action = resp.get("action") if isinstance(resp, dict) else None
    if action == "NONE":
        return []
    if action != "GUARDRAIL_INTERVENED":
        raise GuardrailCallError("응답 형식")
    policies = intervened_policies(resp.get("assessments"))
    if not policies:
        raise GuardrailCallError("판정 없음")  # 개입했다는데 무엇 때문인지 읽을 수 없다
    return policies


def _rows(obj, *path) -> list:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj if isinstance(obj, list) else []


def intervened_policies(assessments) -> list[tuple[str, str]]:
    """INTERVENED 응답의 assessments 에서 개입한 정책 [(범주, 이름)]. 실제 응답 구조의 action 값만 본다.

    BLOCKED 는 정책별 범주로, 개인정보·정규식의 ANONYMIZED 는 "가리기" 로 넣는다. action 이 NONE 인 항목은 넣지 않는다.
    """
    out = []
    for a in assessments if isinstance(assessments, list) else []:
        for t in _rows(a, "topicPolicy", "topics"):
            if t.get("action") == "BLOCKED":
                out.append(("주제", t.get("name", "")))
        for f in _rows(a, "contentPolicy", "filters"):
            if f.get("action") == "BLOCKED":
                out.append(("공격" if f.get("type") == "PROMPT_ATTACK" else "콘텐츠", f.get("type", "")))
        for w in _rows(a, "wordPolicy", "customWords") + _rows(a, "wordPolicy", "managedWordLists"):
            if w.get("action") == "BLOCKED":
                out.append(("낱말", w.get("type", "CUSTOM")))
        for p in _rows(a, "sensitiveInformationPolicy", "piiEntities") + _rows(a, "sensitiveInformationPolicy", "regexes"):
            name = p.get("type") or p.get("name", "")
            if p.get("action") == "BLOCKED":
                out.append(("개인정보", name))
            elif p.get("action") == "ANONYMIZED":
                out.append(("가리기", name))
    return out


def blocked_policies(assessments) -> list[tuple[str, str]]:
    """예전 이름(평가 스크립트 호환). intervened_policies 와 같다."""
    return intervened_policies(assessments)


def decide(policies: Optional[list[tuple[str, str]]], topic_block: bool) -> GuardResult:
    """Guardrails 판정으로 막을지 정한다.

    - 차단 주제는 topic_block 이 꺼져 있으면 기록만 한다. 나머지 BLOCKED 정책은 막는다.
    - "가리기"(이메일·전화번호 ANONYMIZED)만 있어도 막는다(범주 "가리기"). 지금 화면 계약(bool)은 가려진 문장을
      넘기지 못해, 통과시키면 가리기 전 원문이 다음 모델로 간다. 그래서 통과시키지 않는다.
    """
    if not policies:
        return PASS
    usable = [p for p in policies if p[0] not in ("주제", "가리기") or (p[0] == "주제" and topic_block)]
    if usable:
        return GuardResult(True, "guardrails", *usable[0])
    masked = [p for p in policies if p[0] == "가리기"]
    if masked:
        return GuardResult(True, "guardrails", *masked[0])
    logger.info("Guardrails 차단 주제 판정(기록만, 막지 않음): %s", [p[1] for p in policies])
    return PASS


def bedrock_check(text: str, source: str) -> GuardResult:
    """Bedrock Guardrails(ApplyGuardrail). 부를 차례가 아니면 통과, 검사를 끝내지 못하면 막는다(fail-closed, 범주 FAILED)."""
    try:
        policies = bedrock_assess(text, source)
    except GuardrailCallError as e:
        logger.warning("가드레일 검사 실패, 막음(%s)", e)
        return GuardResult(True, "guardrails", FAILED, str(e))
    return decide(policies, topic_block_enabled())


def _check(text: str, source: str) -> GuardResult:
    result = code_check(text) if source == "INPUT" else PASS  # 답에는 코드 검사를 돌리지 않는다
    if not result.blocked:
        result = bedrock_check(text, source)
    if result.blocked:
        logger.info("가드레일 막음: 대상 %s, 단계 %s, 범주 %s, 규칙 %s", source, result.source, result.category, result.rule)
    return result


def is_check_failure(result: GuardResult) -> bool:
    """검사를 끝내지 못해 막은 것인가(설정 누락·호출 실패·해석 불가). 안전상 차단과 구분한다."""
    return result.blocked and result.category == FAILED


def message_for(result: GuardResult, blocked_message: str) -> str:
    """상담에서 차단될 때 표시할 문장. 검사 실패는 UNAVAILABLE, 개인정보 가리기는 PII, 나머지는 blocked_message."""
    if is_check_failure(result):
        return UNAVAILABLE_MESSAGE
    if result.category in ("가리기", "개인정보"):
        return PII_MESSAGE
    return blocked_message


def check_input(text: str) -> GuardResult:
    """사용자 입력 검사(화면 ai.chat.guard_input 과 상담 에이전트 공통). 막히면 모델을 부르지 않는다."""
    return _check(text or "", "INPUT")


def check_output(text: str) -> GuardResult:
    """화면 답변 작성기와 상담 에이전트가 생성한 문장을 검사한다.

    실제 모드에서 GUARDRAIL_OUTPUT=1이면 Bedrock Guardrails를 호출한다.
    화면은 차단되거나 검사를 완료하지 못한 생성 문장을 사용자에게 표시하지 않는다.
    """
    return _check(text or "", "OUTPUT")
