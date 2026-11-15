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

## 파일

| 파일 | 내용 | 저장소 |
| --- | --- | --- |
| `queries.csv` | 세부 능력 → Skill Builder 검색어, 근거 확인용 별칭 | 올림 |
| `data/raw/courses/<과정ID>.md` | 과정 상세 페이지 원문. 맨 위에 출처 주소와 수집일 | 올리지 않음 |
| `data/raw/courses/search_hits.csv` | 어떤 검색어·난이도로 어떤 과정이 몇 위로 나왔는지 | 올리지 않음 |
| `data/courses/courses.csv` | 과정 1개당 1행: 제목, 링크, 난이도, 소요 시간, 언어, 설명, 학습 목표, 수강 대상, 선수지식 | 올림 |
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
