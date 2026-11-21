# AWS 교육과정 수집과 전처리

AWS Skill Builder의 무료 디지털 과정을 수집한다. 기존 직무별 수집기는 추천에 쓸 세부 능력의
상위 과정을 찾고, 확장 수집기는 추천 검색을 검증할 수 있도록 모든 분야와 난이도를 모은다.

## 전체 분야·난이도 수집 (2026-11-20)

```bash
uv run --python 3.11 python -m pipeline.courses.collect_catalog
```

- 클라우드·AI 직무 검색어, 기초·중급 필터, 상위 3개 제한을 쓰지 않는다. 고급, 비즈니스,
  미디어, 게임, IoT 등 다른 주제도 포함한다. 무료 디지털 과정과 영어 원문 조건은 유지한다.
- 웹사이트가 로그인 없이 사용하는 공개 `/graphql` 목록 응답을 읽는다. 새로운 의존성이나 AWS 인증은 필요 없다.
  대상 항목의 학습 목표·선수 지식·분야·난이도·평점과 1~5점별 평가 건수도 같은 응답에 있다.
- 검색 한 번의 조회 창은 100개다. 사이트가 제공하는 분류값으로 나누고 하위 결과의 ID를 합친다.
  분류가 없는 과정도 놓치지 않도록 사이트의 이름 오름차순·내림차순 정렬을 함께 사용한다.
  보고된 전체 건수와 수집한 고유 언어별 과정 ID 수를 대조한다. 일치하지 않으면 결과와 보고서는 남기되 실패로 종료한다.
- 요청은 한 번에 하나씩, 최소 2초 간격으로 보낸다. 429·일시적 서버 오류는 대기 후 제한적으로 재시도하며 `Retry-After`를 따른다.
- 응답은 `data/raw/course_catalog/<UTC 날짜>/`에 저장한다. 중단 뒤 같은 명령을 실행하면 저장한 응답을 재사용한다.
  새 시점의 목록을 받으려면 `--cache data/raw/course_catalog/<새 스냅샷 이름>`으로 새 경로를 지정한다.
- 확장 결과는 `data/courses/catalog_all.csv`, 범위·건수·누락·기존 목록과의 차이는
  `data/courses/catalog_all.summary.json`이다. 평점 수집 시각은 `rating_collected_at`,
  과정 정보 수집 시각은 `catalog_collected_at`에 UTC로 기록한다. 없는 값은 빈 칸이다.
- 기존 서비스 입력인 `courses.csv`, 연결표, 임베딩은 바꾸지 않는다. 확장 CSV에도 같은 기본 열이 있어
  기존 문서 생성기로 평가용 KB 입력을 만들 수 있다. 원문 응답에 있는 피드백 식별자나 개인 학습 기록은 수집하지 않는다.
- 웹사이트 내부의 공개 인터페이스이므로 사이트 개편 시 응답 형식이 바뀔 수 있다. 외부 개발자용 API의 호환성이 보장되는 것은 아니다.

2026-11-20 UTC 스냅샷은 **1,270개**다(기초 588, 중급 458, 고급 224; 기존 목록에 없던 과정 1,056개).
공개 검색의 총계 1,272개와 2개 차이가 남아 `complete=false`이며, 전체 수집 완료로 보지 않는다.
평점은 1,233개에 있고, 평가용 KB에서 추천 가능한 과정은 877개다. 상세 대조는 [데이터 출처](../../docs/data-sources.md)에 있다.

2026-11-20 UTC에 확장 목록과 원본 캐시를 S3에 게시하고 기존 교육과정 KB도 동기화했다.
기존에만 있던 추천 불가 과정 2개를 보존해 KB 문서는 1,272개이며, S3 Vectors에서 벡터 1,485개를 확인했다.
동기화는 `COMPLETE`, 실패·삭제는 0건이다. 기본 추천은 로컬 `courses.csv`의 216개를 사용한다.
상담에서 난이도를 지정하면 같은 폴더의 `catalog_all.csv`도 조회한다. [실행·검증 기록](../../outputs/course_catalog_all/deployment.json)

### 확장 목록을 검색 평가에 준비하기

```bash
uv run --python 3.11 python -m pipeline.build_program_documents \
  --catalog data/courses/catalog_all.csv \
  --output outputs/course_catalog_all/programs
```

이는 로컬 문서와 메타데이터만 만든다. 실제 의미 검색을 비교하려면 확장 문서를 평가용 KB에 넣고,
검색이 읽는 과정 표도 같은 확장 목록으로 맞춰야 한다. 서비스용 KB나 S3를 위 명령으로 갱신하지 않는다.
학습 목표가 없거나 종료 예정인 과정도 목록과 문서에는 보관하지만 `recommendable=false`다.
공개 응답에서 점검 중(`MAINTENANCE`) 등 비활성 상태로 표시된 과정도 평가용 KB에서 추천 대상으로 표시하지 않는다.

다른 분야의 과정은 모든 질문에서 오답인 것이 아니다. 질문과 과정의 학습 목표를 대조해 관련성 정답을 만든다.
기존 `ai/eval/search_gold.json`은 작은 기존 목록을 보고 만든 초안이므로,
새 과정이 정답 목록에 없다는 이유만으로 오추천으로 세면 안 된다. 확장 평가에서는 새 후보의 관련성 판정이 필요하다.

## 기존 직무별 수집 실행

```bash
pip install playwright pandas
playwright install chromium

python pipeline/courses/collect_courses.py --limit 2   # 시험: 검색어 2개만 (약 2분)
python pipeline/courses/collect_courses.py             # 전체: 검색 62회 + 상세 페이지 (약 30분)
python pipeline/courses/build_courses.py               # 전처리 (몇 초)
```

상세 원문은 이미 받은 과정이면 건너뛰므로, 중간에 끊겨도 다시 실행하면 이어서 받는다.

### 평점만 다시 받기 (2026-11-20)

```bash
python pipeline/courses/collect_courses.py --from-catalog --refresh   # 검색 없이 과정 표의 과정 원문만 다시 받는다 (약 30분)
python pipeline/courses/build_courses.py --ratings-only              # 과정 표의 평점 두 열만 채운다
```

- `--from-catalog`는 `data/courses/courses.csv`에 있는 과정의 주소만 연다. 검색을 다시 하지 않으므로 과정 목록이 바뀌지 않는다.
  `--refresh`를 빼면 이미 받은 원문은 건너뛴다.
- `--ratings-only`는 `rating_avg`, `rating_count` 열만 바꾼다. 행 수, 순서, 다른 열이 달라지면 저장하지 않고 멈춘다.
  `course_subskill.csv`와 임베딩(`course_vectors.json`)은 건드리지 않는다. 원문이 없는 과정은 적혀 있던 값을 그대로 둔다.
- 평점은 상세 페이지 Ratings 절의 별점 평균과 평가 수다. 평가가 없는 과정은 빈 칸으로 둔다. 글로 쓴 후기는 페이지에 없다.

### AI 엔지니어 과정 (2026-11-19)

```bash
python pipeline/courses/collect_courses.py --job ai_engineer   # 검색 24회 + 상세 페이지 (약 10분)
python pipeline/courses/build_courses.py --job ai_engineer     # 기존 표에 AI 과정만 덧붙인다
python -X utf8 -m pipeline.build_course_vectors                # 새 과정만 임베딩 (AWS 로그인 필요)
```

- 검색어는 `queries_ai_engineer.csv`(AI 세부 능력 A1-1~A4-3, 12개)다.
- `--job ai_engineer` 전처리는 표를 새로 만들지 않는다. `courses.csv`에 없는 과정만 더하고, `course_subskill.csv`는 A로 시작하는 세부 능력 행만 바꾼다.
  클라우드 원문(11/13 수집분)이 없는 PC에서도 실행할 수 있다. `--job` 없이 실행하면 클라우드 표를 새로 만들므로 클라우드 원문이 있을 때만 쓴다.
- 두 검색어 파일의 별칭은 겹치지 않게 둔다(`pipeline/test_search.py`가 검사). S3·Docker·SQL 같은 공통 기술은 클라우드 세부 능력의 과정으로 이어진다.

## 파일

| 파일 | 내용 | 저장소 |
| --- | --- | --- |
| `queries.csv` | 세부 능력 → Skill Builder 검색어, 근거 확인용 별칭 | 올림 |
| `queries_ai_engineer.csv` | AI 세부 능력 12개의 검색어와 별칭 | 올림 |
| `data/raw/courses/<과정ID>.md` | 과정 상세 페이지 원문. 맨 위에 출처 주소와 수집일 | 올리지 않음 |
| `data/raw/courses/search_hits.csv` | 어떤 검색어·난이도로 어떤 과정이 몇 위로 나왔는지 (AI는 `search_hits_ai_engineer.csv`) | 올리지 않음 |
| `data/courses/courses.csv` | 과정 1개당 1행: 제목, 링크, 난이도, 소요 시간, 언어, 설명, 학습 목표, 수강 대상, 선수지식, 평점(`rating_avg`, `rating_count`) | 올림 |
| `data/courses/course_subskill.csv` | 과정 ↔ 세부 능력 연결, 근거 문장, `certain`/`uncertain` | 올림 |

## 수집 조건

- 주소: `https://skillbuilder.aws/search?typeId=digital_course&accessTier=free&courseLevel=…&searchText=…`
- 디지털 과정, 무료만. 난이도는 기초(`fundamental`)와 중급(`intermediate`)만 (신입 페르소나 기준)
- 검색어·난이도마다 상위 3개. 요청 사이 2초씩 쉬고 한 번에 한 페이지만 연다

## 연결 규칙 (코드로만 판단)

1. 세부 능력 검색어로 검색했을 때 상위 3위 안에 나온 과정만 후보가 된다.
2. 학습 목표 → 제목 → 설명 순서로, 세부 능력 별칭(`queries.csv`의 `aliases`)이 처음 나오는 문장을 근거로 붙인다.
3. 근거 문장이 원문 파일에 그대로 있는지 다시 검사한다.
4. 학습 목표나 제목에서 찾으면 `certain`, 설명에서만 찾았거나 못 찾으면 `uncertain`.

## 한계 (발표와 보고서에 적는다)

- Skill Builder 검색 순위를 그대로 믿는다. 순위가 낮은 좋은 과정은 빠질 수 있다.
- 별칭이 학습 목표에 나와도 그 능력을 깊게 가르치는지는 보장하지 않는다. 최종 20~30개는 사람이 고른다.
- 언어는 상세 페이지의 기본 언어와 "+N more"만 기록한다. 한국어 지원 여부는 링크를 열어 확인해야 한다.
- 난이도는 검색 필터에서 가져온다. 과정 페이지에는 난이도 표기가 없다.
- 수집일 기준 정보다. 과정은 개편되거나 내려갈 수 있다.
- 평점은 수집한 날의 값이고 평가 수가 적은 과정도 있다. 화면에는 평가 수를 함께 보여 주고, 추천 순서에는 쓰지 않는다.
