# 인터페이스 계약

구간 사이를 오가는 데이터 형식과 예시를 정리합니다. 예시 파일은 `data/sample/`에 있습니다.
형식을 바꾸려면 코드보다 먼저 팀 채팅방에 알리고, 이 문서와 예시를 같은 PR에서 고칩니다.

| 형식 | 예시 파일 | 쓰임 |
| --- | --- | --- |
| ① 공고 | `data/sample/posting.json` | 형식 예시 (1건) |
| ② 추출 결과 | `data/sample/extraction.json` | 형식 예시 (1건) |
| ③ 추천 | `data/sample/recommendation.json` | 형식 예시 (1건) |
| ③ 추천 목록 | `data/sample/recommendations.json` | 가짜 모드에서 화면이 읽는 파일 |
| ④ 수요 집계 | `data/sample/demand.json` | 가짜 모드에서 화면이 읽는 파일 |
| ⑤ 교육 공급 | `data/sample/supply.json` | 가짜 모드에서 시장 Gap 계산이 읽는 파일 |
| ⑥ 상담 발화 해석 | `data/sample/chat_turn.json` | 상담에서 갱신할 사실과 이번에 요청한 작업 |
| ⑦ 보유 수준 | `data/skill_levels.json`, `data/required_levels.json` | 세부 능력별 단계 예시, 기술별 기준 단계 |

예시 파일의 숫자는 서로 맞춰 둡니다(공고 40건 기준).

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

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `gap_skill` | string | 부족한 기술의 표준 이름 |
| `demand.postings` | number | 이 기술을 요구한 공고 수 (코드로 계산) |
| `demand.of` | number | 분석한 전체 공고 수 |
| `demand.required_ratio` | number | 요구한 공고 중 필수로 표기한 비율 |
| `courses[].title` | string | 교육과정 이름 |
| `courses[].url` | string | 링크 |
| `courses[].level` | string | 난이도 |
| `courses[].prerequisites` | string | 선수지식 |
| `courses[].reason` | string | 추천 이유 |
| `courses[].match` | string | `certain` 또는 `uncertain` |
| `user_level` | number | (선택, 2026-11-19 추가) 이 기술의 내 단계 0~5. 화면이 단계를 넘겼을 때만 있다. 0~2단계는 기초, 3단계 이상은 중급 과정을 앞에 놓는다 |

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

## ⑤ 교육 공급 (`supply`) — pipeline → backend  · 11/15 추가, 회의 확인 대기

시장 Gap(공고는 원하는데 교육이 덜 다루는 기술)을 계산할 때 쓰는 공급 쪽 집계입니다. 예시는 `data/sample/supply.json`.
계산식은 `backend/api.py`의 `get_market_gap` 설명에 있습니다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | string | 교과목 데이터 출처 |
| `total_courses` | number | 전체 교과목 수 |
| `note` | string | 화면에 함께 보여줄 안내(한계 등). 없으면 생략 |
| `skills[].skill` | string | 표준 기술 이름 |
| `skills[].courses` | number | 이 기술을 다루는 교과목 수 (코드로 계산) |

## ⑦ 보유 수준 — app → backend · 2026-11-19 추가(#90)

사용자는 세부 능력(`pipeline/courses/queries.csv`의 `C1-1` 등 31개)마다 0~5단계를 고른다. 0은 해 본 적 없음이다.
단계마다 "이 정도면 할 수 있는 일" 예시는 `data/skill_levels.json`에 있다(AI 초안, 사람이 확인한 세부 능력만 `reviewed: true`).

보유 입력(`get_gap`, `get_area_gap`, `owned_skill_names`의 두 번째 인자)은 둘 중 하나다.

| 형식 | 예 | 뜻 |
| --- | --- | --- |
| `{세부 능력 id: 0~5}` | `{"C5-2": 3, "C1-4": 2}` | 수준 진단. 없는 id 는 0. 모르는 id 나 범위 밖 값은 오류 |
| 기술 이름 목록(예전 형식) | `["Python", "Linux"]` | 목록의 기술은 5단계, 나머지는 0. 상담 채팅이 쓴다 |

기술의 내 단계는 그 기술이 걸린 세부 능력 단계 중 가장 높은 값이다. 별칭으로 연결되지 않는 기술은 `skill_subskills` 표를 쓰고,
`ALL`(AWS 같은 공통 기술)은 1단계 이상으로 고른 세부 능력 단계의 평균이다.

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
