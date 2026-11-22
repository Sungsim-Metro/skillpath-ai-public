# ai/

채용공고에서 기술을 근거 문장과 함께 뽑고, 상담 발화를 해석하고, 입력을 가드레일로 검사하고, 추천 이유를 쓰고 검토합니다. 결과는 평가 도구로 잽니다. (담당: 성진)

**원칙: 세기는 코드, 이해는 AI**
- 빈도, 비율, Gap(집합 비교), 순위, 점수 평균, 일치율은 모두 코드로 계산합니다. LLM에게 숫자를 세게 하지 않습니다.
- LLM은 공고에서 기술을 뽑고, 상담 발화를 해석하고, 추천 이유와 답을 씁니다.
- LLM이 쓴 문장은 코드가 검사합니다.
  - 근거 문장이 공고 원문에 있는지 확인합니다(`evidence.py`).
  - 추천 이유의 숫자가 입력 숫자와 맞는지 확인합니다(`recommend.py`의 `validate_reasons`).
  - 답에 공격 문구가 있는지 확인합니다(`guardrail.check_output`).
- 검사를 통과하지 못하면 결과를 버리거나 `uncertain`(불확실)으로 남깁니다.

## 흐름

```text
[공고 분석]
 공고 ─▶ 기술 추출 (extract.py, LLM) ─▶ 근거 검사 (evidence.py, 코드)
          │                            └ 원문에 없는 근거 → 버리거나 uncertain
          └ 칸(section) 찾기·가중치 (sections.py, 코드)
      ─▶ 표준화 (pipeline/standardize.py, 별칭 사전, 코드)
      ─▶ 수요 집계 (backend/, 코드)

[학습 추천]
 부족 기술 (backend/api.py get_gap, 코드)
      ─▶ 과정 검색 (pipeline/search.py, recommend.py candidate_courses)
      ─▶ 추천 이유 작성 (recommend.py, LLM: reason만 작성)
      ─▶ 독립 검토와 한 번의 수정 (recommend_review.py, LLM, 채팅 추천)
      ─▶ 코드 검사 (validate_reasons: 숫자·과정 id) ─ 통과한 과정만 표시

[학습 상담]
 사용자 발화 ─▶ 가드레일 (guardrail.py: 코드 검사 → Bedrock Guardrails, 실패하면 막음)
      ├─ 화면 경로 (구조 0)   : 발화 해석 (chat.py, LLM, 모의 모드는 chat_mock.py)
      │                          → backend/chat.py 가 계산 → 답 작성 (chat_reply.py) → 답 검사
      ├─ 팀장 구조 (구조 3)   : supervisor.py (일현 구조, AB_STRUCTURES 로 켬)
      └─ 상담 에이전트 (구조 A): agent.py (평가용, 화면에서는 부르지 않음)
```

## 모듈

| 파일 | 역할 | 어디서 부르나 |
| --- | --- | --- |
| `bedrock.py` | Bedrock Converse 호출, JSON 검증과 재시도, 모의 응답, 모델 고르기 | `ai/` 전체, `backend/api.py`, `backend/chat.py`, `backend/supervised_courses.py`, `pipeline/extraction_batch.py`, `pipeline/wanted_dataset.py` |
| `extract.py` | 공고 → 추출 결과(② 기술·근거·confidence). 근거 검사와 칸 가중치를 붙임 | `backend/api.py`(공고 직접 분석), `pipeline/extraction_batch.py`, `pipeline/restandardize.py` |
| `evidence.py` | 근거 문장이 공고 원문에 있는지 코드로 검사 | `extract.py` |
| `sections.py` | 공고 본문의 칸(자격요건·우대사항 등) 찾기와 칸 기준 가중치 | `extract.py`, `backend/api.py`, `backend/industry_gap.py`, `pipeline/standardize.py`, `pipeline/rocketpunch.py` |
| `recommend.py` | 부족 기술 → 추천(③). 과정 후보 고르기, LLM이 reason만 작성, `validate_reasons` 검사, 과정 정보·평점 읽기 | `backend/api.py`, `backend/experiments.py` |
| `recommend_review.py` | 채팅 추천의 이유 작성 → 독립 검토 → 한 번 수정(학습 목표 원문과 대조) | `recommend.py`(검토를 켰을 때) |
| `guardrail.py` | 입력·답 검사. 코드 검사(공격 문구, 속성+판단) 뒤 Bedrock ApplyGuardrail. 호출이 실패하면 막음(fail-closed) | `chat.py`, `chat_reply.py`, `agent.py` |
| `chat.py` | 상담 발화 해석: 보유 기술, 요청 종류, 저장 범위. 해석 결과를 원문과 대조해 검증 | `backend/chat.py` |
| `chat_mock.py` | 모의 모드에서 쓰는 로컬 규칙 해석기 | `chat.py` |
| `chat_reply.py` | 상담 답 작성. 프로필과 계산 결과를 바꾸지 않고, 답 검사를 거침 | `backend/chat.py` |
| `supervisor.py` | **일현 구조(3번)**. 발화 해석, 팀장의 실행 전 검토, 결과 검토 | `backend/chat.py`(구조 3일 때) |
| `supervisor_mock.py` | **일현 구조(3번)**의 모의 모드 해석 | `supervisor.py` |
| `agent.py` | 상담 에이전트(구조 A, #25). Converse 도구 호출을 반복하는 방식이고, 도구는 `backend/api.py` 함수를 그대로 부름 | `ai/eval/agent_compare.py`, `ai/eval/supervised_compare.py`(평가 전용) |
| `usage.py` | 호출마다 토큰 기록(`usage`)과 세션 집계 | `bedrock.py`, `backend/chat.py`, `backend/usage.py`, `app/chat.py`, `pipeline/embed.py` |

평가 도구(`ai/eval/`):

| 파일 | 하는 일 |
| --- | --- |
| `score.py` | 추출 정답지 채점(정밀도·재현율·F1). 채점용 공고(#21)는 `--final`로 한 번만 |
| `run_raw.py`, `compare_runs.py` | 정답 없는 개선용 공고로 추출을 돌리고, 프롬프트 버전끼리 비교 |
| `make_gold_template.py` | 채점용 공고의 빈 정답지 양식 만들기와 검사(본문은 출력하지 않음) |
| `recommend_demo.py` | 추천 이유 v1 데모(검사 결과와 토큰) |
| `guardrail_eval.py` | 가드레일 오탐률·미탐률 |
| `agent_compare.py` | 상담 구조 0·A 비교(의도, 되묻기, 가드레일 차단, 호출, 토큰, 시간). 답 채점과 일치율도 이 명령으로 실행 |
| `judge_answer.py`, `agreement.py` | 상담 답 채점(#94)과 LLM·사람 채점 일치율. 기준표는 [JUDGE_ANSWER.md](eval/JUDGE_ANSWER.md) |
| `supervised_compare.py` | 구조 A(PR #121 고정)와 팀장 구조의 개발용 대화 비교 |

## 프롬프트 버전

| 파일 | `VERSION` | 쓰는 곳 |
| --- | --- | --- |
| `prompts/extract_v1.py` | `extract_v1` | 11/22 전까지의 기본값. 대표명 12개 안만 high |
| `prompts/extract_v2.py` | `extract_v2` | 비교용(`score.py --prompt v2`) |
| `prompts/extract_v2_1.py` | `extract_v2_1` | 채점용 공고(#21) 고정 버전(`score.py --split test`), `pipeline/restandardize.py`의 기본 후처리 |
| `prompts/extract_v3.py` | `extract_v3` | **추출 기본값**(`extract.py`의 `DEFAULT_PROMPT = "v3"`, 11/22). v2.1 + 규칙 두 줄(Network, IaC 같은 범주 이름). 두 직무의 화면 데이터를 이 버전으로 다시 추출함(항목 35) |
| `prompts/recommend_v1.py` | `recommend_v1` | 추천 이유(`recommend.py`) |
| `prompts/agent_v1.py` | `agent_v1.2` | 상담 에이전트(구조 A) |
| `prompts/judge_answer_v1.py` | `judge_answer_v1` | 상담 답 채점(도움 정도·근거 충실도 1~5점) |

## 실행

저장소 루트에서 실행합니다. 가짜 모드(`BEDROCK_MOCK=1`)에서는 AWS를 호출하지 않습니다.

```bash
# 테스트 (가짜 모드). 모듈 하나씩 실행하고, 통과하면 "통과"가 출력됨
BEDROCK_MOCK=1 python -m ai.test_guardrail
BEDROCK_MOCK=1 python -m ai.test_judge_answer
for f in ai/test_*.py; do m=${f%.py}; BEDROCK_MOCK=1 python -m ${m//\//.} || echo "실패: $m"; done

# 평가 (가짜 모드)
BEDROCK_MOCK=1 python -m ai.eval.score --runs 1                  # 추출 F1 (가짜 10건)
BEDROCK_MOCK=1 python -m ai.eval.guardrail_eval                  # 가드레일, 코드 검사만
BEDROCK_MOCK=1 python -m ai.eval.agent_compare --structures 0 A  # 상담 구조 비교
BEDROCK_MOCK=1 python -m ai.eval.agent_compare --judge outputs/eval/agent_answers_<...>.json --rounds 2 --human-sheet
BEDROCK_MOCK=1 python -m ai.eval.agent_compare --agreement outputs/eval/agent_judge_<...>.json <사람 채점 CSV>
```

PowerShell에서는 `$env:BEDROCK_MOCK="1"; .venv\Scripts\python -m ai.eval.score --runs 1`처럼 실행합니다.

**실제 모드** (`BEDROCK_MOCK=0`)
- AWS SSO 로그인이 먼저 필요합니다: `aws sso login --profile <프로필>`, `AWS_PROFILE` 설정
- 상담 구조 비교는 호출 한도를 주지 않으면 시작하지 않습니다. 한도 직전에 멈추고, 그때까지의 결과를 저장합니다.
  ```bash
  python -m ai.eval.agent_compare --structures 0 A --bedrock --max-model-calls 100 --max-guardrail-calls 30
  python -m ai.eval.agent_compare --judge <답 파일> --bedrock --rounds 2 --max-judge-calls 40 --human-sheet
  ```
- 채점 모델 `JUDGE_MODEL_ID`는 답을 쓴 모델 `BEDROCK_MODEL_ID`와 달라야 합니다. 같거나 비어 있으면 멈춥니다.
- `score.py`와 `guardrail_eval.py`에는 호출 한도 옵션이 없습니다. 각각 공고 수×횟수, 문항 수만큼 호출합니다.
  ```bash
  python -m ai.eval.score --runs 3
  python -m ai.eval.guardrail_eval --bedrock            # 문항마다 ApplyGuardrail 1번
  ```
- 보지 않은 문항은 `--set holdout`(`agent_compare`), `--holdout`(`guardrail_eval`), `--split test --final`(`score`)로 실행합니다. 화면에는 숫자만 나옵니다.
- 결과 파일은 `outputs/eval/`에 저장되고 커밋하지 않습니다.

## 환경 변수

값은 `.env`에만 둡니다. 이름은 [.env.example](../.env.example)에 있습니다.

| 이름 | 쓰는 곳 |
| --- | --- |
| `BEDROCK_MOCK` | 가짜 모드(1)와 실제 모드(0) |
| `AWS_PROFILE`, `AWS_REGION` | AWS 접속(리전 기본값 `ap-northeast-2`) |
| `BEDROCK_MODEL_ID` | 추출, 추천 이유, 상담에 쓰는 모델 |
| `EMBED_MODEL_ID` | 과정 검색 임베딩(`pipeline/`) |
| `JUDGE_MODEL_ID` | 상담 답 채점 모델(평가 전용) |
| `GUARDRAIL_ID`, `GUARDRAIL_VERSION` | Bedrock Guardrails. 비어 있으면 예전 이름 `BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`을 씀 |
| `GUARDRAIL_TOPIC_BLOCK` | 차단 주제로도 막을지(기본 1) |
| `GUARDRAIL_OUTPUT` | 답도 Guardrails로 검사할지(기본 0, 코드 검사는 늘 함) |
| `USAGE_LOG` | 토큰 기록 파일 위치(`off`면 끔) |
| `BEDROCK_KB_ID` | 과정 검색의 지식 기반(`pipeline/search.py`, `backend/api.py`) |
| `EXPERIMENT_MODELS`, `AB_STRUCTURES` | 상담 비교 실험(`backend/experiments.py`). 비어 있으면 꺼짐 |

## 평가 결과 요약

숫자는 [IMPROVEMENT_LOG.md](eval/IMPROVEMENT_LOG.md)에서 가져왔습니다. 모두 1회 측정이고, 조건은 각 항목에 있습니다.

| 영역 | 결과 | 조건 | 출처 |
| --- | --- | --- | --- |
| 추출 F1 (개선용) | v1 0.900 → v2.1 **0.977** (P 1.000, R 0.955) | 가짜 공고 10건, 정답 44개, 3회 같음 | 항목 5 |
| 추출 불확실 비율 (개선용) | 56.2% → **11.6%** | dev 공고 12건, v1 → v2.1(사전 86) | 항목 5 |
| 추출 F1 (채점용 공고, #21) | P 0.885, R 0.798, **F1 0.839** (이름만 F1 0.883). 재현율 사전 안 0.915, 사전 밖 0.490 | 고칠 때 보지 않은 공고 50건, 정답 746개(종훈 작성, 1인), v2.1, 1회 | 항목 34 |
| 지어낸 근거 | 가짜 10건 **0** / 채점용 50건 **1**(코드가 근거 검사에서 걸러 uncertain 처리) | 가짜: v1, 3회 / 채점용: v2.1, 1회 | 항목 1, 34 |
| 가드레일, 보지 않은 10문항 | 오탐 **0/5**, 미탐 **0/5**, 검사 실패 0. 코드 검사만 쓰면 미탐 5/5 | 실제 모드, Guardrail 버전 2 + 코드 검사, 11/21 | 항목 30 |
| 가드레일, 개선용 33문항 | 미탐 2/18 → **0/18** | 버전 2 모든 정책 + 코드 검사, 11/19 | 항목 20 |
| 가드레일, 정상 질문 막힘 | 한 줄 정상 질문 12개 중 4개 막힘(모두 Guardrails 차단 주제), 여러 줄 0개 | 실제 모드, 버전 2, 11/21 | 항목 30 |

**상담 구조 0(화면 경로) 대 A(상담 에이전트): 보지 않은 12문항**

| 지표 | 구조 0 | 구조 A | 출처 |
| --- | --- | --- | --- |
| 도움 정도(helpful) LLM / 사람 | 3.38 / 2.38 | **4.38 / 4.62** | 항목 33 |
| 근거 충실도(grounded) LLM / 사람 | 3.75 / 3.5 | **4.62 / 5.0** | 항목 33 |
| 의도 정확도 | 7/8 | 10/10 | 항목 33 |
| 모델 호출 / 토큰 | 28 / 60,072 | 25 / 75,557 | 항목 33 |
| 시간 중앙값 | 4.1초 | 6.9초 | 항목 33 |
| 불확실 과정 비율 | 0.0 | 0.5 | 항목 33 |

| LLM·사람 일치율 (n=16) | 정확 일치 | ±1 일치 | kappa (quadratic) | 출처 |
| --- | --- | --- | --- | --- |
| helpful | 0.5 | 0.75 | 0.634 | 항목 33 |
| grounded | 0.62 | 0.81 | 0.422 | 항목 33 |

같은 답을 두 번 채점한 16쌍 중 합계가 달라진 쌍은 1개입니다(항목 33). 개발용 문항의 결과는 항목 32에 있습니다.

## 평가 데이터 규칙

- **개발용 문항**은 보고 고치는 데 씁니다. 여기서 나온 점수는 과대평가로 적습니다.
  - 추출: `data/sample/`의 가짜 공고와 [GOLD_RULES.md](eval/GOLD_RULES.md)에 따른 정답지
  - 가드레일: `data/eval/guardrail_cases.json`
  - 상담: `data/eval/agent_cases.json`
- **보지 않은 문항**은 규칙을 고정한 뒤 한 번만 돌립니다. 내용을 열거나 출력하지 않고, 그 결과를 보고 고치지 않습니다.
  - 가드레일: `data/eval/guardrail_cases_holdout.json`
  - 상담: `data/eval/agent_cases_holdout.json`
  - 추출: `data/raw/eval_test/`(채점용 공고와 정답지)
- `data/eval/`와 `data/raw/`는 공개 저장소에 올라가지 않습니다(`.github/public-sync/allowlist.txt`에 없음). `data/raw/`는 `.gitignore`에 있어 커밋도 하지 않습니다.
- 답 원문, 근거 자료, 사람 채점 CSV는 `outputs/eval/`에만 저장합니다.

## 한계

- **추출:**
  - F1 0.977은 개선용 가짜 공고 10건의 점수이고, 같은 사람이 만든 정답지 기준입니다. 채점용 50건 F1은 0.839입니다(항목 34).
  - 채점용 정답 기술의 28%가 별칭 사전 밖이고, 사전 밖 기술의 재현율은 0.490입니다(항목 34).
  - 화면과 수요 집계는 두 직무 모두 v3 추출입니다(11/22 적용, 기본값 v3, 항목 35). 채점용 F1 0.839는 v2.1 기준이고, v3에는 정답지 기반 F1이 없습니다. v3는 개발용 데이터와 정답 없는 새 공고 50건에서 v2.1과 비교만 했습니다.
  - 기본값이 v3라 입력 지문이 달라져, `pipeline/extraction_batch.py`를 다시 돌리면 두 직무 전체를 v3로 다시 추출합니다(약 380만 토큰, 항목 35).
- **가드레일:**
  - 보지 않은 10문항은 예전에 열람된 적이 있습니다(항목 31).
  - Guardrails 차단 주제는 자기 상황을 밝힌 정상 질문 일부도 막습니다(항목 30·31).
  - 코드 검사는 세 줄 이상으로 나눈 요청을 다 잡지 못합니다(항목 30).
- **상담 구조 비교:**
  - 구조당 8문항, 1회 측정이고, 사람 채점은 한 사람이 했습니다.
  - 16항목이라 kappa의 신뢰구간이 넓습니다.
  - 근거 충실도 해석이 사람과 LLM 사이에 다릅니다(질문과 무관하지만 지어낸 내용이 없는 답).
  - LLM이 구조 0의 도움 정도를 사람보다 후하게 줬습니다.
  - A의 불확실 과정 비율이 0.5입니다(항목 33).
- **상담 에이전트(구조 A)는 평가용입니다.** 화면 상담은 구조 0(설정하면 3)으로 처리합니다.
