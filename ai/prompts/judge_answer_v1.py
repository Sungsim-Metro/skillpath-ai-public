"""상담 답 채점 지시문 v1 (#94). 채점 모델(JUDGE_MODEL_ID)이 상담 답 하나를 기준표로 1~5점 매긴다.

- 답을 쓴 구조(0·A)는 알려 주지 않는다. 질문, 답, 근거 자료(그 답을 만들 때 코드·도구가 낸 결과 요약)만 준다.
- 점수만 받는다. 평균·분포·잡음·구조 차이는 코드가 계산한다(원칙 2).
- 기준표(RUBRIC)는 사람 채점 문서 ai/eval/JUDGE_ANSWER.md 의 표와 글자까지 같아야 한다(ai/test_judge_answer.py).
  기준표를 바꾸면 VERSION 을 올리고 두 곳을 함께 고친다.
- 앵커 문장(5·3·1점)은 11/19 구조 비교 계획의 기준표 안 그대로다. 2점과 4점은 그 사이.
"""
from __future__ import annotations

import json

VERSION = "judge_answer_v1"
ITEM_ID = "answer"  # 한 번에 답 하나만 채점한다. 응답의 item_id 는 늘 이 값이다

# (키, 이름, 5점, 3점, 1점)
RUBRIC = (
    ("helpful", "도움 정도",
     "질문에 바로 답하고, 다음에 무엇을 공부할지 구체적으로 알 수 있다",
     "질문에 답하지만 일반적이거나, 다음 행동이 모호하다",
     "질문에 답하지 않거나 엉뚱한 답이다"),
    ("grounded", "근거 충실도",
     "답의 숫자·기술·과정이 모두 도구 결과에 있고, 불확실한 것은 불확실하다고 밝힌다",
     "대체로 도구 결과에 있지만 확인되지 않는 내용이 하나 있다",
     "도구 결과에 없는 숫자·과정을 지어내거나 단정한다"),
)
CRITERIA = tuple(r[0] for r in RUBRIC)


def rubric_table() -> str:
    """기준표 마크다운 표. 지시문과 사람용 문서가 같은 표를 쓴다."""
    lines = ["| 키 | 이름 | 5점 | 3점 | 1점 |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| `{k}` | {name} | {five} | {three} | {one} |" for k, name, five, three, one in RUBRIC]
    return "\n".join(lines)


SYSTEM = f"""당신은 채용공고 기반 학습 상담 서비스의 답을 채점하는 평가자입니다.
상담 질문, 상담 답, 근거 자료를 받습니다. 근거 자료는 그 답을 만들 때 코드와 도구가 낸 결과(공고 수, 부족 기술, 추천 과정과 이유 등)의 요약입니다.

아래 기준표로 두 기준을 각각 1~5 정수로 매깁니다. 2점과 4점은 그 사이입니다.

{rubric_table()}

채점 규칙
- 근거 충실도는 답의 숫자·기술 이름·과정 이름을 근거 자료와 하나씩 대조해 매깁니다. 근거 자료에 없는 내용은 확인되지 않은 내용입니다.
- 근거 자료가 비어 있으면, 답이 숫자·과정을 단정하지 않을 때만 높게 줄 수 있습니다.
- 답의 길이나 말투는 점수에 넣지 않습니다.
- 숫자를 세거나 평균을 내지 않습니다. 점수만 매깁니다.

출력은 JSON 객체 하나만 씁니다. 다른 글은 쓰지 않습니다.
{{"scores": [{{"item_id": "{ITEM_ID}", "helpful": 1~5 정수, "grounded": 1~5 정수, "note": 낮게 준 이유 한 문장 또는 null}}]}}
"""


def user_message(question: str, answer: str, evidence: str) -> str:
    return json.dumps({"item_id": ITEM_ID, "question": question, "answer": answer, "evidence": evidence},
                      ensure_ascii=False)
