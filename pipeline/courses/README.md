# AWS 교육과정 수집과 전처리

역량 분류 체계(`docs/skill-taxonomy.md`)의 세부 능력 31개마다 AWS Skill Builder에서 무료 과정을 찾고,
과정 상세 페이지 원문을 저장한 뒤 표로 정리한다. 수집기는 팀에 공유된 Playwright 예시 크롤러를 바탕으로 고쳤다.

## 실행

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
