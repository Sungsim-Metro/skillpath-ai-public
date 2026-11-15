# 인터페이스 계약

구간 사이를 오가는 데이터는 아래 세 형식만 씁니다. 예시 파일은 `data/sample/`에 있습니다.
형식을 바꾸려면 코드보다 먼저 팀 채팅방에 알리고, 이 문서와 예시를 같은 PR에서 고칩니다.

| 형식 | 예시 파일 | 쓰임 |
| --- | --- | --- |
| ① 공고 | `data/sample/posting.json` | 형식 예시 (1건) |
| ② 추출 결과 | `data/sample/extraction.json` | 형식 예시 (1건) |
| ③ 추천 | `data/sample/recommendation.json` | 형식 예시 (1건) |
| ③ 추천 목록 | `data/sample/recommendations.json` | 가짜 모드에서 화면이 읽는 파일 |
| ④ 수요 집계 | `data/sample/demand.json` | 가짜 모드에서 화면이 읽는 파일 |
| ⑤ 교육 공급 | `data/sample/supply.json` | 가짜 모드에서 시장 Gap 계산이 읽는 파일 |

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
가중치는 코드가 계산한다(`ai/sections.py`의 `weight`). 같은 공고에 같은 기술이 여러 번 나오면 가중치가 가장 큰 것 하나만 ②에 남긴다.

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
