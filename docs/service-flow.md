# 서비스 플로우

2026-11-12 회의, 2026-11-13 결정 반영. 그림은 Mermaid 텍스트라서 GitHub에서 이 파일을 열면 바로 그림으로 보입니다.
고치려면 파일 오른쪽 위 연필 아이콘을 누르고 아래 코드 블록의 글자를 바꾼 뒤 PR을 올립니다.
구조나 JSON 형식이 바뀌는 수정은 [`contracts/`](../contracts/README.md)와 같은 PR에서 함께 고칩니다.

## 1. 사용자 흐름

취업 준비생이 보유 기술을 고르고 추천을 받기까지의 화면 순서입니다.

```mermaid
flowchart TD
    start(["접속"]) --> login["손님으로 이용하거나<br/>닉네임과 비밀번호로 프로필 열기"]
    login --> side["사이드바<br/>목표 직무 확인<br/>수준 진단 · 역량 직접 입력"]
    side --> t1["탭 1 시장 수요<br/>요구 기술 순위 · 필수/우대<br/>공고 수와 출처"]
    t1 --> ev["기술을 누르면<br/>추출 근거 문장 보기<br/>상세는 원본 공고 링크"]
    t1 --> t2["탭 2 부족 역량<br/>부족 기술을 수요 높은 순으로<br/>영역별 충족률"]
    t2 --> t3["탭 3 학습 추천<br/>AWS 교육과정 · 추천 이유<br/>학습 순서 · 링크 · 난이도"]
    t3 --> t5["탭 5 학습 상담<br/>무엇부터 공부할까?<br/>Bedrock Agent"]
    side -.-> t4["탭 4 공고 직접 분석 · 보류<br/>공고를 붙여 넣으면<br/>기술과 근거 추출"]

    classDef hold stroke-dasharray: 5 4
    class t4 hold
```

- 점선 칸은 아직 확정되지 않았거나 보류한 부분입니다. 탭 4는 보류를 유지합니다(2026-11-12). 손님은 현재 세션에서 이용하고, 등록한 프로필은 비밀번호로 다시 열 수 있습니다.
- 발표의 세 질문과 화면의 대응: 어떤 데이터를 분석했나 → 탭 1, 어떤 근거로 필요하다 판단했나 → 탭 1의 근거 문장과 탭 2, 왜 이 과정을 추천했나 → 탭 3.
- 근거 문장은 추출 결과에서만 보여줍니다. 추천 화면에는 수요 수치와 추천 이유가 붙습니다(2026-11-12).
- 공고 상세 내용은 화면에 옮기지 않고 원본 공고 링크로 보냅니다. 링크는 탭 1 근거 문장 옆에 붙입니다(2026-11-15). 링크가 없는 공고는 공고 id만 보입니다.

## 2. 시스템 구조

공고가 왼쪽에서 들어와 오른쪽 화면에서 추천으로 나옵니다. 화살표의 ①~④는 구간 사이를 오가는 JSON 형식 번호입니다.

```mermaid
flowchart LR
    subgraph SRC["데이터 · 종훈"]
        posting["채용공고<br/>로켓펀치 · 사람인 대기<br/>사기업 공고만"]
        ncs["NCS 능력단위"]
        course["AWS 교육과정<br/>SkillBuilder 목록에서 선별<br/>과정명 · 링크 · 난이도"]
    end

    s3[("S3<br/>API 응답 JSON<br/>요약 + 원본 링크")]
    extract["기술 추출<br/>Lambda + Bedrock Claude"]
    verify["근거 검증<br/>근거 문장이<br/>원문에 있나"]
    normalize["표현 표준화<br/>별칭 사전 + Titan 임베딩"]
    ddb[("DynamoDB<br/>추출 결과 · 수요 집계<br/>사용자가 저장한 프로필")]
    gap["수요 집계 · Gap 계산<br/>3단 구분 가중치<br/>LLM 없이 코드로"]
    search["교육과정 검색<br/>Knowledge Base<br/>S3 Vectors"]
    reason["추천 이유 생성<br/>Bedrock Claude<br/>수치만 입력"]
    agent["학습 상담 에이전트<br/>Bedrock Agent + Guardrails"]
    app["Streamlit 화면"]

    posting --> s3
    s3 -- "① 공고" --> extract
    extract --> verify
    verify --> normalize
    ncs --> normalize
    normalize -- "② 추출 결과" --> ddb
    ddb --> gap
    gap -- "④ 수요 집계" --> app
    app -- "보유 기술" --> gap
    gap -- "부족 기술" --> reason
    course --> search
    search --> reason
    reason -- "③ 추천" --> app
    app -- "질문" --> agent
    agent -. "도구 호출" .-> gap
    agent -. "도구 호출" .-> search

    classDef ai fill:#f1ecfd,stroke:#6b3fd4,color:#18202b
    classDef store fill:#eaf3ee,stroke:#2f7a52,color:#18202b
    classDef ui fill:#e9f1fc,stroke:#1f5fbf,color:#18202b
    class extract,search,reason,agent ai
    class s3,ddb store
    class app ui
```

| 색 | 뜻 |
| --- | --- |
| 보라 | AI가 하는 일 (Bedrock) |
| 초록 | 저장소 |
| 파랑 | 화면 |
| 색 없음 | 코드가 하는 일, 데이터 원천 |

구간별 담당과 이슈는 [WBS](wbs.md)에, JSON 형식은 [`contracts/`](../contracts/README.md)에 있습니다.

### 구조에서 지키는 것

1. **세기는 코드, 이해는 AI.** 보라색 칸만 Bedrock을 부릅니다. 빈도, 비율, Gap은 집계 코드가 계산하고 추천 이유는 그 숫자를 받아 쓰기만 합니다.
2. **근거 문장은 추출 단계에서만 씁니다.** 추출 직후 근거 문장이 공고 원문에 실제로 있는지 코드로 확인하고, 없으면 버리거나 "불확실"로 표시합니다.
3. **S3에는 API 응답 JSON을 저장합니다.** 요약 정보와 원본 공고 링크가 들어 있고, 근거 검증·프롬프트 재실행·정확도 측정에 같은 데이터를 씁니다. 공고 웹페이지 원문 전체는 저장하지 않습니다. 원본은 수정될 수 있어 상세는 링크로 보냅니다(2026-11-12).
4. **학습 상담 에이전트는 이미 만든 함수를 도구로 부르기만 합니다.** 수요 조회와 과정 검색 두 개이고, Guardrails가 주제 밖 질문을 막습니다.

### 아직 정해지지 않은 것

- 복합 Gap 정의
- 3단 구분(자격 요건, 주요 업무, 우대 사항)의 값 이름과 가중치 수치
- 역량 분류(8개 영역)를 데이터에 붙이는 방식
