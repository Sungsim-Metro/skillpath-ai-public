# SkillPath AI

채용공고에서 기술 수요를 근거 문장과 함께 뽑고, 내 보유 기술과 비교해 부족한 역량과 AWS 교육과정을 추천 이유와 함께 보여주는 AI 에이전트입니다.

AI Bootcamp Innovation Challenger 2026 · 팀 ASAP하게

> 이 저장소는 팀 개발 저장소(비공개)에서 공개할 수 있는 파일만 자동으로 옮긴 사본입니다. 개발 중이라 내용이 계속 바뀝니다.
> 최종 동기화: 2026-11-20 15:19 KST (개발 저장소 커밋 b951a46)

## 어떤 문제를 푸나

클라우드·AI 엔지니어를 준비하는 취업 준비생은 "공고가 실제로 무엇을 요구하는지"와 "그래서 무엇부터 공부해야 하는지"를 한눈에 보기 어렵습니다.
SkillPath AI는 실제 채용공고를 분석해 세 가지 질문에 답합니다.

1. 어떤 데이터를 분석했나: 실제 공고 몇 건에서 어떤 기술이 몇 번 요구됐는지
2. 어떤 근거로 필요하다고 판단했나: 기술마다 공고 원문의 근거 문장
3. 왜 이 과정을 추천했나: 부족한 기술마다 AWS 교육과정과 추천 이유

## 사용 흐름

1. 목표 직무를 고르고 보유 기술을 입력합니다.
2. 채용공고에서 뽑은 요구 기술을 수요가 높은 순으로 봅니다. 기술마다 근거 문장이 붙습니다.
3. 보유 기술과 비교한 부족 역량(Skills Gap)을 봅니다.
4. 부족 역량별 AWS 교육과정을 추천 이유와 함께 봅니다.

## 처리 흐름

```
공고 수집 → 기술 추출 → 표현 표준화 → 분석·Gap → 교육과정 추천 → 화면
(pipeline)    (ai)      (pipeline)   (backend)   (ai + pipeline)   (app)
```

| 폴더 | 내용 |
| --- | --- |
| `app/` | Streamlit 화면 |
| `backend/` | Lambda, DynamoDB, 수요 집계와 Gap 계산 |
| `pipeline/` | 데이터 수집, 전처리, 별칭 사전, 임베딩 검색 |
| `ai/` | 추출·추천 프롬프트, 가드레일, 정확도 평가 |
| `contracts/` | 구간 사이를 오가는 JSON 형식 |
| `data/sample/` | 개발용 가짜 데이터 |

그림으로 보려면 [`docs/service-flow.md`](docs/service-flow.md)를 엽니다.

## AWS 서비스와 쓰는 이유

| 서비스 | 어디에 | 왜 |
| --- | --- | --- |
| Amazon Bedrock (Claude) | 기술 추출, 추천 이유 생성 | 표현이 일정하지 않은 공고 문장을 읽고 설명하는 일 |
| Titan Embeddings | 표현 표준화 | 같은 뜻의 다른 표현을 의미로 묶기 |
| Bedrock Knowledge Base | 교육과정 검색 | 부족 기술에 맞는 과정 설명을 의미로 찾기 |
| Bedrock Agents | 학습 상담 | 수요 조회와 과정 검색 함수를 도구로 불러 답 구성 |
| Bedrock Guardrails | 추출, 학습 상담 | 주제 밖 질문과 차별 조건 차단 |
| S3 | 공고 API 응답 저장 | 근거 검증, 프롬프트 재실행, 정확도 측정에 같은 데이터 사용 |
| Lambda | 수집·추출 자동 처리 | 서버 없이 필요할 때만 실행 |
| DynamoDB | 추출 결과, 학습 계획 | 닉네임별 조회 |

## 설계 원칙

- **세기는 코드, 이해는 AI.** 빈도, 비율, Gap은 코드로 계산합니다. LLM은 문장 이해와 설명에만 씁니다.
- **근거 없는 결과는 내보내지 않습니다.** 추출한 기술마다 근거 문장이 있고, 그 문장이 공고 원문에 실제로 있는지 코드로 검사합니다.
- **확신이 없으면 "불확실"로 표시합니다.** 그럴듯하게 채우지 않습니다.
- **구간 사이 데이터는 정해진 JSON 형식만 씁니다.** 형식은 [`contracts/`](contracts/README.md)에 있습니다.

## 무엇을 어디서 보나

| 보고 싶은 것 | 위치 |
| --- | --- |
| 문제를 어떤 단계로 풀었나 | [`docs/service-flow.md`](docs/service-flow.md), [`contracts/`](contracts/README.md) |
| 추출 프롬프트와 근거 검증, 정확도 채점 | [`ai/`](ai/README.md) |
| 어떤 데이터를 썼고 한계는 무엇인가 | [`docs/data-sources.md`](docs/data-sources.md) |
| 팀이 어떻게 역할을 나누고 협업했나 | [`COLLABORATION.md`](COLLABORATION.md) |
| 회의에서 무엇을 결정했나 | [`docs/meeting-decisions.md`](docs/meeting-decisions.md) |

## 실행

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
BEDROCK_MOCK=1 streamlit run app/main.py
```

`BEDROCK_MOCK=1`이면 AWS 계정 없이 `data/sample/`의 가짜 데이터로 화면이 뜹니다.
