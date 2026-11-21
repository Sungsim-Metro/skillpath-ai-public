# 인터페이스 계약

구간 사이를 오가는 데이터 형식과 예시를 정리합니다. 예시 파일은 `data/sample/`에 있습니다.
형식을 바꾸려면 코드보다 먼저 팀 채팅방에 알리고, 이 문서와 예시를 같은 PR에서 고칩니다.

| 형식 | 예시 파일 | 쓰임 |
| --- | --- | --- |
| ① 공고 | `data/sample/posting.json` | 형식 예시 (1건) |
| ② 추출 결과 | `data/sample/extraction.json` | 형식 예시 (1건) |
| ③ 추천 | `data/sample/recommendation.json` | 형식 예시 (1건) |
| ③ 추천 목록 | `data/sample/recommendations.json` | 학습 추천 화면의 모의 데이터(`get_recommendations`의 `review=False` 경로) |
| ④ 수요 집계 | `data/sample/demand.json` | 가짜 모드에서 화면이 읽는 파일 |
| ⑤ 교육 공급 | `data/sample/supply.json` | 기술별 학습 프로그램 수를 기록하는 형식 예시 |
| ⑥ 상담 발화 해석 | `data/sample/chat_turn.json` | 상담에서 갱신할 사실과 이번에 요청한 작업 |
| ⑦ 보유 수준 | `data/skill_levels.json`, `data/required_levels.json` | 세부 능력별 단계 예시, 기술별 기준 단계 |
| ⑧ 직군별 시장 Gap | `data/sample/market_postings.json`, `data/courses/courses.csv` | AI·클라우드 엔지니어 각각의 기술 수요와 ⑤의 공급 비중 비교 |

④의 모의 파일은 공고 40건을 가정한 가상 집계입니다. ⑤의 형식 예시는 과정 48개를 가정한 가상 집계입니다. ⑧의 계산 예시는 직군이 표시된 가상 공고를 실제 학습 프로그램 목록 전체와 비교합니다.

## ① 공고 (`posting`) — pipeline → ai

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `id` | string | 출처 접두어 + 번호. 예: `rp-159502`. ②·④의 `posting_id`가 이 값을 가리킨다 |
| `company` | string | 회사명 |
| `title` | string | 공고 제목 |
| `industry` | string \| null | 산업. 로켓펀치는 산업 코드 그대로(예: `FINANCE_ETC`) |
| `role` | string | 직무 이름. 예: `클라우드 엔지니어` |
| `career` | string \| null | 경력·숙련도 원문(예: `JUNIOR,MIDLEVEL`). 분석에는 쓰지 않는다 |
| `region` | string \| null | 근무 지역. 소스가 주지 않으면 `null` |
| `posted_at` | string \| null | 게시일 `YYYY-MM-DD`(한국시간) |
| `text` | string | 공고 본문 전체. 근거 문장 검사의 기준 |
| `url` | string \| null | 원본 공고 링크. 수동 수집처럼 링크가 없으면 `null` |

필드 이름은 수업 실습 노트북(`01_데이터수집_전처리`)의 표준 필드를 따릅니다. 데이터 출처와 수집일은 필드로 두지 않고 `docs/data-sources.md`에 적습니다(2026-11-15).

## ② 추출 결과 (`extraction`) — ai → backend

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `posting_id` | string | ①의 id |
| `skills[].raw` | string | 공고에 쓰인 표현 그대로 |
| `skills[].normalized` | string | 표준 이름. 예: `AWS` |
| `skills[].type` | string | `required` 또는 `preferred` |
| `skills[].section` | string \| null | 근거 문장이 들어 있는 공고 칸. `qualification`(자격요건), `duty`(주요업무), `preference`(우대사항), `other`(표에 없는 칸 제목이나 첫 칸 제목 앞의 글), `null`(칸 제목이 없는 공고, 또는 근거 문장을 본문에서 못 찾음). 코드가 정한다 |
| `skills[].evidence` | string | 근거 문장. ①의 `text`에 그대로 있어야 함 |
| `skills[].confidence` | string | `high` 또는 `uncertain` |
| `skills[].ncs_unit` | string \| null | 연결된 NCS 능력단위. 없으면 `null` |

**칸(section)** (2026-11-15 추가): ①의 `text`는 칸마다 `[주요업무]`, `[자격요건]`, `[우대사항]`처럼 제목을 한 줄에 단독으로 쓴다(수업 4주차 실습 방식).
코드(`ai/sections.py`)가 근거 문장이 어느 칸 제목 아래에 있는지 보고 `section`을 정한다. 혜택·회사소개처럼 지원자에게 요구하지 않는 칸에서 나온 기술은 ②에 넣지 않는다.

**수요 가중치** (2026-11-15 팀 결정, 회의록 추가 예정): 자격요건 1.0 / 주요업무 0.7 / 우대사항 0.5(수업 5주차 실습 값).
칸이 있으면 칸 기준, 칸이 없거나 모르는 칸이면 type 기준(required 1.0 / preferred 0.5).
가중치는 코드가 계산한다(`ai/sections.py`의 `weight`). 추출기는 같은 공고에서 같은 `normalized`의 후보가 여러 번 나오면 다음 순서로 하나만 ②에 남긴다.

1. 공고 근거와 대표명 검사를 통과한 `confidence: "high"` 후보를 우선한다.
2. 확신도가 같으면 수요 가중치가 가장 큰 후보를 고른다.
3. 확신도와 가중치가 모두 같으면 먼저 나온 후보를 고른다.

예를 들어 ID가 `sample-0002`이고 본문이 다음과 같은 공고에서 LLM이 `AWS`를 필수와 우대 후보로 각각 낼 수 있다.

```text
[우대사항]
AWS 운영 경험 우대
```

필수 후보의 근거가 본문에 없는 `AWS 운영 경험 필수`라면 `required`, `uncertain`이고 가중치는 1.0이다.
우대 후보의 근거가 `AWS 운영 경험 우대`라면 `preferred`, `high`이고 가중치는 0.5이다.
이때 ②에는 우대 후보를 남긴다. 결과 예시는 `data/sample/extraction_deduplicated.json`에 있다.

## ③ 추천 (`recommendation`) — backend + ai → app

③은 추천 모듈이 **기술 하나에 대해** 반환하는 데이터 형식이다. `get_recommendations`는 이 형식의 목록을 반환한다. 추천할 기술을 고르는 방법과 이 결과로 사용자에게 답하는 방법은 호출 경로에 따라 달라진다. [추천 한 건의 예시](../data/sample/recommendation.json)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `gap_skill` | string | 추천 대상 기술 이름. 개인 Gap에서 고른 기술 또는 상담에서 과정을 요청한 기술 |
| `demand` | object \| null | 해당 직무의 수요 수치. 학습 추천 화면과 Gap 기반 추천은 아래 세 필드가 있는 객체를 받는다. 3번 상담이 특정 기술을 조회했을 때 수요를 확인할 수 없으면 내부 추천 결과에서만 `null`이 된다 |
| `demand.postings` | number | 이 기술을 요구한 공고 수 (코드로 계산) |
| `demand.of` | number | 분석한 전체 공고 수 |
| `demand.required_ratio` | number | 요구한 공고 중 필수로 표기한 비율 |
| `courses[].title` | string | 교육과정 이름 |
| `courses[].url` | string | 링크 |
| `courses[].level` | string | 난이도 |
| `courses[].prerequisites` | string | 선수지식 |
| `courses[].reason` | string | 추천 이유 |
| `courses[].match` | string | `certain` 또는 `uncertain` |
| `courses[].rating_avg` | number | (선택, 2026-11-20 추가) AWS Skill Builder 과정 페이지의 별점 평균(0 초과 5 이하). 평가가 없거나 읽지 못한 과정에는 이 필드와 `rating_count`가 없다 |
| `courses[].rating_count` | number | (선택, 2026-11-20 추가) 그 평균을 낸 평가 수(1 이상 정수). `rating_avg`와 항상 함께 있다 |
| `courses[].duration_min` | number | (선택, 2026-11-21 추가) 과정 소요 시간(분, 1 이상 정수). 과정 표에 없으면 이 필드가 없다 |
| `courses[].language` | string | (선택, 2026-11-21 추가) 강의 언어. 과정 페이지의 표기 그대로다(예: `English`) |
| `courses[].aws_services` | string[] | (선택, 2026-11-21 추가) 과정 페이지가 적은 AWS 서비스 이름 목록. 없으면 이 필드가 없다 |
| `user_level` | number | (선택) 호출자가 이 기술의 수준을 전달했을 때만 들어가는 내 단계(0~5) |

`courses`는 추천 순서대로 놓이며, 연결할 과정이 없으면 빈 목록이다. 해당 기술의 수준을 전달하면 직접 다루는 과정을 먼저 두고, 같은 매칭 상태 안에서 3단계 미만은 기초, 3단계 이상은 중급 과정을 우선한다. 수준이 없으면 기존 순서를 유지한다. 카드 화면은 기술별로 이 순서에 1위부터 번호를 붙인다.
평점은 수집한 날의 값을 코드가 과정 페이지에서 읽은 것이고(`pipeline/courses/build_courses.py`의 `parse_rating`), 화면에 보여 주기만 한다. 추천 순서와 추천 이유에는 쓰지 않고 LLM 입력에도 넣지 않는다.
소요 시간·강의 언어·AWS 서비스 이름도 같다(`ai/recommend.py`의 `course_info`). 과정 표(`data/courses/courses.csv`)에 이미 있는 값을 카드에 보여 주기만 한다. 설문에서 "과정 정보가 부족해 판단하기 어렵다"는 응답이 많아 넣었다. 선수지식은 과정 페이지의 문장이라 화면에는 "있음/없음"만 적고, 과정 페이지에 항목이 없으면("표기 없음") 적지 않는다(#92, 원칙 4).

### 호출 경로별 추천 동작

| 경로 | 추천 대상 | 결과 사용 방식 |
| --- | --- | --- |
| 학습 추천 화면 | 개인 Gap에서 기준 단계에 미달한 기술 | 기술별 수준을 전달하고 ③의 수요와 과정을 탭에 표시한다. 기본 호출은 `review=False`다. |
| 기본 상담(0번) | 개인 Gap의 상위 부족 기술 | 기본은 `review=True`다. 추천 수치가 Gap 계산에 쓴 수치와 일치하는지 확인하고 과정 카드를 표시한다. |
| 도구 호출형 상담(1번, 비교 평가용) | 에이전트가 추천 도구에 넘긴 기술 중 직무 수요 집계에 있거나 사용자가 이번 메시지에서 이름을 언급한 기술 | 도구가 `review=True`로 추천을 받아 과정 제목·난이도·매칭 상태·이유를 에이전트에 전달한다. 에이전트가 최종 답변을 글로 작성한다. |
| 팀장형 상담(3번, `AB_STRUCTURES=3`) | 이번 요청에서 지목한 기술. “그 기술”처럼 앞선 대화에서 대상을 확인한 경우도 포함한다. 일반적인 과정 추천 요청에는 개인 Gap을 사용한다. | `review=True`로 과정별 추천 이유를 검토한다. 팀장이 요청과 결과가 맞는지 승인하면 검증된 이유와 링크를 카드로 표시한다. |

기본 상담에서 `AB_STRUCTURES=single,reviewed`를 설정하면 같은 Gap 기술로 두 방식의 추천을 만든다. 실제 모드에서 `single`은 추천 이유를 작성한 뒤 코드로 검사하고, `reviewed`는 별도 모델로 초안을 검토해 필요한 경우 한 번 수정한 뒤 최종 근거를 검사한다. 두 방식 모두 ③ 형식을 사용하며 양쪽에 표시할 과정이 있을 때 카드 투표를 연다. [추천 구조 등록과 선택](../backend/experiments.py)

기본 상담에서 `AB_STRUCTURES=reviewed,3`을 설정하면 과정 추천 턴마다 같은 메시지를 3번 팀장 구조로 한 번 더 처리해 두 결과를 카드 투표로 보여 준다(2026-11-20). 상담 상태와 답 글은 기본 구조의 것을 쓰고, 팀장 구조는 턴 전 상태의 사본에서 돌리며 프로필에 쓰지 않는다. 팀장 구조가 과정을 내지 못했거나 검증에 실패하면 투표를 열지 않는다. 형식(③, `alternatives`)은 바뀌지 않는다.

3번이 특정 기술의 과정을 요청받으면 직무 집계에서 확인한 수치만 사용한다. 예를 들어 Kubernetes 과정을 요청했는데 수요 행이 없으면 내부 추천 결과는 `gap_skill="Kubernetes"`, `demand=null`이다. 이때는 확인된 과정 학습 목표로 추천 이유를 구성하고, 상담 답변에는 수요 자료가 없다고 안내한다. 상담 화면은 `Reply.text`와 과정별 `Reply.courses`를 받는다. [특정 기술 추천 처리](../backend/supervised_courses.py)

## ④ 수요 집계 (`demand`) — backend → app  · 11/10 회의 승인 대기

시장 수요 탭에서 직무 전체의 기술 순위를 보여주기 위한 형식입니다. 예시는 `data/sample/demand.json`.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `job_category` | string | 직무 코드 |
| `job_title` | string | 화면에 보여줄 직무 이름 |
| `total_postings` | number | 분석한 전체 공고 수 |
| `sources` | string[] | 데이터 출처 목록 |
| `collected` | string | 수집일 또는 기간 |
| `skills[].skill` | string | 표준 기술 이름 |
| `skills[].postings` | number | 이 기술을 요구한 공고 수 (코드로 계산) |
| `skills[].required` | number | 그중 필수로 표기한 공고 수 |
| `skills[].preferred` | number | 그중 우대로 표기한 공고 수 |
| `skills[].evidence_samples[]` | object[] | 근거 예시 `{posting_id, url, evidence}` 1~3개. `url`은 원본 공고 링크이고, 링크가 없는 공고(수동 수집)는 `null`이거나 생략 |

화면은 `url`이 있으면 근거 문장 옆에 "공고 원문 열기" 링크를 붙입니다(2026-11-15 추가).

## ⑤ 교육 공급 (`supply`) — pipeline → backend

채용공고 없이 학습 프로그램 목록만으로 전체 과정 수와 기술별 과정 수를 집계합니다. 프로그램의 제목(`title`), 설명(`description`), 학습 목표(`objectives`)에 같은 기술이 여러 번 등장해도 해당 과정은 그 기술에 한 번만 셉니다. 한 과정이 여러 기술을 다루면 각 기술에 한 번씩 셉니다.

`backend.api.get_supply()`의 반환 형식과 `outputs/learning_program_supply.json`의 저장 형식은 같습니다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | string | 집계한 프로그램 목록의 출처 |
| `total_courses` | integer | 조사한 전체 학습 프로그램 수 |
| `skills[].skill` | string | 표준 기술 이름 |
| `skills[].courses` | integer | 해당 기술이 검출된 프로그램 수 |
| `note` | string | 선택 사항. 조사 범위나 자료의 한계 |

예를 들어 Docker의 교육 공급량은 `skills[]`에서 `skill`이 `Docker`인 행의 `courses`로 확인합니다. `total_courses`에는 기술이 하나도 검출되지 않은 과정도 포함합니다. [프로그램 목록](../data/courses/courses.csv)

## ⑦ 보유 수준 — app → backend · 2026-11-19 추가(#90)

사용자는 세부 능력(클라우드는 `pipeline/courses/queries.csv`의 `C1-1` 등 31개, AI는 `data/skill_levels_ai_engineer.json`의 `A1-1` 등 12개)마다 0~5단계를 고른다. 0은 해 본 적 없음이다.
세부 능력 id는 직무끼리 겹치지 않는다. 보유 입력 하나에 두 직무의 진단이 함께 들어가고, 계산은 고른 직무의 표로 한다(2026-11-19).
문항을 진단표에서 제거하면 저장소는 예전 설문 ID와 응답을 보존하고 현재 계산에서는 제외한다. 같은 ID를 다시 등록하면 이전 응답이 복원되므로 문항의 의미가 바뀌면 새 ID를 사용한다.
단계마다 "이 정도면 할 수 있는 일" 예시는 `data/skill_levels.json`에 있다(AI 초안, 사람이 확인한 세부 능력만 `reviewed: true`).

보유 입력(`get_gap`, `get_area_gap`, `owned_skill_names`의 두 번째 인자)은 둘 중 하나다.

| 형식 | 예 | 뜻 |
| --- | --- | --- |
| 단계 사전 | `{"C5-2": 3, "A1-1": 2, "custom:OpenTelemetry": 4}` | 직무 진단은 기존 세부 능력 ID를 쓰고, 직접 입력한 역량은 `custom:` 뒤에 이름을 붙인다. 단계는 0~5의 정수다. |
| 기술 이름 목록(예전 형식) | `["Python", "Linux"]` | 기존 호출과의 호환을 위해 목록의 기술은 5단계, 나머지는 0으로 계산한다. 대시보드와 상담의 공고 비교에는 단계 사전을 전달한다. |

직접 입력한 이름은 양끝 공백을 제거하고 알려진 별칭을 대표명으로 통일한다. 예를 들어 `도커`와 `Docker`는 `custom:Docker` 한 항목이다. 사전에 없는 역량은 처음 입력한 이름을 보관하며, 대소문자만 바꿔 다시 저장하면 기존 단계를 갱신한다. 공고에 같은 기술이 있으면 직접 입력한 단계(0 포함)를 우선한다. [프로필 예시](../data/sample/profile.json)

기술 이름과 직접 입력한 단계는 모든 목표 직무가 공유한다. 상담에서 “OpenTofu를 사용했어요”라고 밝히고 저장을 요청하면 `skills`에 `OpenTofu` 한 건을 기록한다. 숫자 단계는 수준을 직접 입력했을 때 `levels`의 `custom:OpenTofu`에 기록한다. `C8-1`(과금 체계 이해) 같은 ID는 해당 설문 문항에 응답했을 때만 생긴다. 새 직무에서 같은 기술을 요구하면 같은 보유 정보를 그 직무의 수요와 기준 단계에 대조한다.

상담은 사용자가 기술을 직접 썼다고 새로 말하면 그 기술의 대표명·등록된 별칭과 현재 직무의 문항 연결표로 응답할 문항을 찾는다. 여러 기술이 같은 문항에 연결되면 한 번만 제시한다. 전체 문항에 걸친 연결(`ALL`), 등록된 연결이 없는 기술, 관련 도구와의 유사도나 임베딩으로 추정한 연결은 질문 대상에서 제외한다.

이미 답한 문항은 0을 포함한 현재 단계가 선택된 채로 다시 제시한다. 연결된 기술에 직접 입력한 단계가 있으면 기술별 항목도 현재 값을 선택해 표시한다. 예를 들어 클라우드 문항 `C1-4`(컨테이너)와 `custom:Docker`는 각각 수정할 수 있고, Docker의 Gap 계산에는 `custom:Docker` 값이 우선한다. 사용자가 0~5단계를 골라 적용 버튼을 누르면 새로 답하거나 바꾼 키만 로그인한 사용자의 `profile.levels` 또는 손님의 현재 세션에 반영한다. 선택을 그대로 두거나 질문을 건너뛰거나 화면을 재실행해도 DB에 단계를 쓰지 않는다.

상담은 기술 이름과 경험을 기록하고 요약한다. 공고와의 Gap과 과정 추천에는 사용자가 선택한 단계 사전을 쓴다. 사용 경험을 밝힌 기술에 직접 입력한 단계나 적용할 설문 응답이 없으면 상담에 ‘수준 미입력’으로 표시하고 그 기술의 추천을 보류한다.

직접 입력한 단계가 없으면 그 기술이 걸린 세부 능력 단계 중 가장 높은 값을 사용한다. 별칭으로 연결되지 않는 기술은 `skill_subskills` 표를 쓰고,
`ALL`(AWS 같은 공통 기술)은 그 직무의 세부 능력 중 1단계 이상으로 고른 단계의 평균이다. AI 직무에서 클라우드와 겹치는 기술(Docker 등)은 두 직무의 세부 능력 중 높은 단계다.

사이드바와 상담은 인증된 세션으로 프로필을 읽고 쓴다. 반환값은 `job`, `skills`, `experiences`, `levels` 네 필드다. 비밀번호 검증 정보는 이 반환값과 모델 입력에서 제외한다.

담은 교육과정은 같은 프로필 항목의 `saved_courses`에 `[{"title", "url", "level"}]`로 담은 순서대로 둔다(2026-11-20). 주소는 https만 받고 같은 주소는 한 번만 담으며 한 사람당 50개까지다. `backend.chat_profiles`의 `load_saved_courses`, `update_saved_courses`로만 읽고 쓰고, 위 네 필드의 반환값과 모델 입력에는 넣지 않는다. 비회원이 담은 과정은 저장하지 않는다.

Gap 행(`get_gap`)에 더한 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `user_level` | number | 내 단계 0~5(공통 기술은 소수일 수 있음) |
| `required_level` | number | 기준 단계 1~5. `data/required_levels.json`에 없으면 3 |
| `required_level_confidence` | string | `certain`(공고 3건 이상으로 정함) 또는 `uncertain`(기본값) |
| `satisfaction` | number | 충족도 0~1 = 필수 비중 × (내 단계 ≥ 기준 단계 ? 1 : 0) + 우대 비중 × 내 단계/5 |
| `owned` | boolean | 내 단계 ≥ 기준 단계 |

`get_area_gap`의 `owned`는 기술마다 요구 공고 수 × 충족도를 더한 값이라 소수일 수 있다.

`get_area_gap_after(job, 보유 입력, 추천 목록)`(레이더 "마친 뒤", 2026-11-19 #91)은 `get_area_gap`과 같은 영역 행에 필드 하나를 더한다.
추천 목록은 ③ `get_recommendations` 결과이고, `match`가 `certain`인 과정만 마친 것으로 본다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `raised` | array | `[{"skill", "from", "to"}]` 이 영역에서 단계가 오르는 기술. 예상 단계는 과정 난이도로 정한다(기초 2, 중급 3, `backend/levels.py`의 `AFTER_COURSE_LEVEL`). 지금이 더 높으면 오르지 않아 넣지 않는다 |

기준 단계(`data/required_levels.json`)는 `pipeline/required_levels.py`가 만든다. 공고 근거 문장(②의 `evidence`, 수요 집계와 같이 `confidence`가 `high`인 것)을
단계 예시와 임베딩으로 비교해 공고별 요구 단계를 정하고, 기술별 중앙값을 코드로 계산한다. 파일에는 집계 숫자만 쓰고 공고 문장은 넣지 않는다.

## ⑥ 상담 발화 해석 (`Turn`) · ai → backend

모델은 이번 발화에서 요청한 작업과 사용자 본인의 사실을 반환합니다. 백엔드가 지원 직무·저장 대상·호출 범위를 확인합니다. 아래 예시는 가상의 사용자가 직접 맡은 Docker 배포 경험을 저장하고 부족한 기술을 물은 경우입니다. [발화 해석 예시](../data/sample/chat_turn.json)

| 필드 | 형식과 의미 |
| --- | --- |
| `status` | `ok`, `blocked`, `unsupported`, `clarify_role`, `incomplete`, `out_of_scope`. `ok`일 때만 사실과 작업을 적용합니다. |
| `action` | `note`(입력), `summary`(정리), `gap`(공고와 비교), `courses`(과정 추천·검증). |
| `job` | 이번에 명시하거나 확인한 지원 직무 코드 또는 `null`. |
| `add_skills`, `remove_skills` | 세션에 추가하거나 정정할 기술 이름 목록. |
| `add_experiences`, `remove_experiences` | 사용자가 직접 맡은 일과 정정 대상인 기존 경험 문구 목록. |
| `experience_skills` | 보존된 프로젝트 경험에서 본인이 직접 사용했다고 밝힌 기술. 이번 응답의 비교·요약에 사용하며, 이 필드만으로 프로필의 보유 기술을 수정하지 않습니다. |
| `save`, `save_job` | 명시적 프로필 쓰기 요청 여부와 직무가 저장 대상인지 나타내는 불리언. |
| `save_skills`, `save_experiences` | 프로필 쓰기에서 명시적으로 지정한 항목 목록. 정정과 함께 지정하면 해당 항목을 삭제합니다. |

### 팀장 상담의 실행 계획

`ai.supervisor.interpret_turn`은 원문, 직전 대화, 이번 해석을 팀장이 대조한 뒤 `SupervisedTurn`을 반환한다. `turn`은 위 형식이고 `wishes`는 이번 턴을 반영한 세션의 학습 희망, `targets`는 이번에 과정을 요청한 기술이다. 대상 확인이 필요하면 `question`을 표시하고 다음 발화를 기다린다. 희망이나 과정 요청만으로 보유 기술·수준·저장 동의를 설정하지 않는다. `decisions`는 실행 전 팀장의 결정 순서다. 백엔드는 `Reply.supervisor_decisions`에 워커 결과의 승인·거절·실패까지 기록한다. [학습 희망을 참조한 계획 예시](../data/sample/supervised_turn.json)

## ⑧ 직군별 시장 Gap

각 직군에 속한 공고의 본문에서 기술별 최대 구역 가중치를 구하고, 같은 직군의 전체 기술 가중치 합을 기준으로 수요 비중을 계산합니다. 두 직군에 속한 공고는 각 직군에서 한 번씩 집계합니다. 두 직군 모두 ⑤에서 구한 전체 학습 프로그램의 기술별 공급 비중과 비교합니다.

`backend.api.get_market_gap(job_category)`은 선택한 직군의 `rows`와 자료 출처를 표시하는 `source`를 반환합니다. 배치 CSV는 두 직군을 모두 담고, 대시보드에서 내려받는 CSV는 선택한 직군의 행을 담습니다.

| 필드 | 의미 |
| --- | --- |
| `job_category`, `skill` | 직군 코드(`ai_engineer`, `cloud_engineer`)와 대표 기술명 |
| `demand_pct` | 해당 직군 안에서 기술이 차지하는 가중 수요 비중 (%) |
| `supply_pct` | ⑤의 해당 기술 과정 수 ÷ 모든 기술의 과정 수 합계 × 100 |
| `gap_pp` | 반올림 전 수요 비중과 공급 비중의 차이 (%p). 세 수치는 각각 소수 첫째 자리로 반올림합니다. |

현재 과정 목록과 예시 공고로 검산하려면 `python -m pipeline.market_gap --example`을 실행합니다. `outputs/example/learning_program_job_gap_table.csv`에서 `job_category`가 `cloud_engineer`이고 기술이 `Docker`인 행의 수요 비중, 공급 비중, Gap을 확인할 수 있습니다. [입력 경로와 갱신 방법](../pipeline/README.md#직군별-시장-gap)
