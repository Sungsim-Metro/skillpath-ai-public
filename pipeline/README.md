공고·NCS·교육과정 수집, 전처리, 별칭 사전, 임베딩 검색. (담당: 종훈)

## 분석에 쓰는 채용공고

분석 입력은 `data/raw/rocketpunch-2026-11-17/postings-reviewed/` 아래 두 폴더다. `cloud/`는 클라우드 엔지니어, `ai/`는 AI 엔지니어 공고다. 분류는 모델이 공고의 근거 문장을 확인한 결과이며, 해당 시점의 수량은 [데이터 출처](../docs/data-sources.md)에 기록한다.

검토본은 Git에서 제외되므로 새 작업 환경에는 같은 경로에 데이터를 준비한다. 직무별 수요 배치는 해당 폴더의 공고를 전부 사용한다. 두 직무에 속한 공고는 각 직무에서 한 번씩 센다. 전체 추출 점검(`python -m ai.eval.run_raw`)에서는 같은 ID를 한 번만 분석한다. 양쪽에 있는 ID는 `ai/`의 파일을 대표로 읽는다. `python -m pipeline.rocketpunch export-eval`도 같은 모집단에서 튜닝 공고와 중복 재공고를 제외하고 표본을 뽑는다.

## 채용공고 수집과 S3 보관

`rocketpunch.py`는 로켓펀치 API 응답을 먼저 로컬 `data/raw/`에 저장한다.
`ROCKETPUNCH_S3_BUCKET`을 설정하면 `search`, `fetch`, `collect` 실행 중에도 S3에 자동 업로드한다.
버킷을 설정하지 않으면 기존처럼 로컬에만 저장한다. AWS 인증은 `AWS_PROFILE` 등 SDK 기본 설정을 따른다.
계정·버킷 이름과 API 키는 코드에 넣지 않고, 저장소에서 제외된 루트 `.env`에 설정한다.

```dotenv
AWS_PROFILE=<your-profile>
AWS_REGION=ap-northeast-2
ROCKETPUNCH_API_KEY=<your-api-key>
ROCKETPUNCH_S3_BUCKET=<your-bucket>
ROCKETPUNCH_S3_PREFIX=skillpath/rocketpunch
```

S3 경로는 다음과 같다. S3의 디렉터리는 객체 키의 접두사다.

```text
<prefix>/
  raw/lists/list-<검색조건>/<내용의 SHA-256>.json
  raw/details/rp-<공고ID>/<내용의 SHA-256>.json
  processed/postings/rp-<공고ID>/<내용의 SHA-256>.json
```

- `raw/lists`: 채용공고 목록 API 응답. 새로 수집한 목록은 `items`, `totalItems`, `totalPages` 등 응답 전체를 보존한다.
- `raw/details`: 공고별 상세 API 응답. 직무 필터에서 탈락한 공고도 원본은 보관한다.
- `processed/postings`: 검토된 두 폴더의 공고. 두 직무에 속한 공고는 `role`과 내용 해시가 달라 각 버전이 보존된다.
- 같은 파일 내용은 같은 키를 사용해 중복 저장하지 않는다. 내용이 달라지면 새 객체로 남기므로 이전 근거를 재현할 수 있다.
  [S3 조건부 PUT](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)으로 기존 객체 덮어쓰기를 막는다.
- JSON은 줄바꿈을 `\n`으로 고정해 저장한다. 키가 파일 바이트의 해시라서, OS 기본 줄바꿈에 맡기면 Windows에서 저장한 파일만 다른 키가 된다.
  업로드에는 디스크에서 읽은 파일 바이트를 그대로 사용한다.
- 기존 로컬 파일은 이동하거나 삭제하지 않는다. 캐시를 읽을 때도 S3 업로드를 시도하므로 이전 업로드 실패를 재시도할 수 있다.
- S3 실패 시 로컬 파일을 남기고 오류를 보고한다. 같은 명령이나 `upload` 명령으로 다시 시도한다.
- 예전에 저장한 목록 7개는 `items` 배열만 남아 있다. 그대로 보관하고 호환해서 읽으며, 없어진 총 건수·총 페이지 수는 추정하지 않는다.

### 기존 파일 업로드

다음 명령은 API를 다시 호출하지 않는다. `.env`가 없으면 `--bucket <your-bucket>`과 `AWS_PROFILE` 환경변수로도 지정할 수 있다.

```powershell
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch upload --dry-run
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch upload
```

대상은 `data/raw/` 바로 아래의 목록·상세 API 캐시와 `postings-reviewed/ai/`, `postings-reviewed/cloud/`의 전처리 공고다.

### 이후 수집

```powershell
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch search -k DevOps --codes dev
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch collect -k DevOps --codes dev -n 10
```

기존 캐시는 API를 다시 호출하지 않는다. 새 공고 응답을 가져오면 로컬 저장 후 S3에도 보관한다.
수집기를 수동으로 실행할 때 동작한다. 아래 DynamoDB 설정을 추가하면 `fetch`, `collect`가 끝난 뒤
지정한 직무의 검토본을 대상으로 기술 추출과 수요 집계를 실행한다. 새 후보는 도메인 검토를 거쳐 해당 폴더에 들어간 뒤부터 분석에 포함된다.

`search`와 `collect`는 검색 조건·페이지가 같은 `data/raw/list-*.json` 목록 캐시를 재사용한다.
최신 목록을 받으려면 목록에서 해당 파일 한 개를 골라 `.bak`로 옮긴 뒤 같은 수집 명령을 다시 실행한다.
다음은 위 DevOps 검색의 첫 페이지 캐시를 백업하는 예다. 갱신 대상은 선택한 목록 캐시이며,
상세 원본(`rp-*.raw.json`)과 전처리 공고(`rp-*.posting.json`)는 계속 재사용한다.

```powershell
Get-ChildItem -LiteralPath data/raw -Filter 'list-*.json' | Select-Object Name
$listCache = 'data/raw/list-jobCategories=dev-keyword=DevOps-page=1-pageSize=10-sort=DATE_DESC.json'
Move-Item -LiteralPath $listCache -Destination "$listCache.bak"
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch search -k DevOps --codes dev
```

## DynamoDB 추출 결과와 수요 집계

API 목록·상세 응답과 전처리 공고는 위 S3 경로에 보관한다. DynamoDB에는 검증된 기술 추출 결과와
완성된 수요 집계를 저장한다. 기존 ② 추출 결과와 ④ 수요 집계 형식은 각각 `extraction`, `demand` 안에 유지한다.

루트 `.env`에 다음 설정을 채운다. 테이블 이름은 환경변수로 주입하며 아래 이름은 설계의 예시다.

```dotenv
BEDROCK_MOCK=0
BEDROCK_MODEL_ID=<your-model-id>
ROCKETPUNCH_S3_BUCKET=<your-bucket>
DYNAMODB_EXTRACTIONS_TABLE=skillpath-posting-extractions
DYNAMODB_DEMAND_TABLE=skillpath-demand
```

테이블은 AWS 콘솔에서 만들거나 다음 명령으로 생성한다. 이미 있는 테이블은 키 구성을 확인하고 재사용한다.
서울 리전, 온디맨드 과금이며 추출 테이블의 키는 `job_category`와 `posting_id`, 집계 테이블의 키는 `job_category`다.

```powershell
uv run --python 3.11 --with boto3 python -m backend.dynamodb create-tables
```

이후 수집은 한 명령으로 로컬·S3 저장, Bedrock 추출, DynamoDB 저장과 수요 집계까지 실행한다.
`--collected`에는 `docs/data-sources.md`에 기록한 수집일 또는 기간을 넣는다.

```powershell
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch fetch 159625 --collected "2026-11-17~2026-11-19 (이전 캐시 포함)"
uv run --python 3.11 --with boto3 python -m pipeline.rocketpunch collect -k DevOps --codes dev -n 10 --collected "2026-11-17~2026-11-19 (이전 캐시 포함)"
```

검토된 공고를 직무별로 분석해 AWS에 게시할 때는 아래 명령 중 해당 직무를 실행한다. 수집 API 호출은 필요 없다.

```powershell
uv run --python 3.11 --with boto3 python -m pipeline.extraction_batch --job-category cloud_engineer --collected "2026-11-17~2026-11-19 (이전 캐시 포함)"
uv run --python 3.11 --with boto3 python -m pipeline.extraction_batch --job-category ai_engineer --collected "2026-11-17~2026-11-19 (이전 캐시 포함)"
```

- 두 테이블 이름이 모두 비어 있으면 수집은 기존 로컬·S3 저장까지 실행한다. 하나라도 설정하면 두 테이블과 S3 버킷,
  실제 모델 설정을 모두 검사하고 수집 후 저장 배치를 실행한다. 가짜 모드에서는 저장 배치를 시작하지 않는다.
- 분석 목록은 배치 시작 시 직무별 검토 폴더에서 고정한다. S3에서 같은 바이트를 다시 읽어 분석하고 객체 키와 본문 해시를 기록한다.
- 기본 선정 버전은 `rocketpunch-reviewed-v1`이다. 독립 배치에 `--terms`를 지정하면 검토본 중 제목이 일치하는 공고로 범위를 좁히고 필터 해시를 버전에 반영한다. 수집기의 `--terms`는 수집 후보만 고른다.
- 같은 본문·프롬프트·모델·추출기·설정의 성공 결과는 재사용한다. `--force`를 추가하면 같은 입력도 다시 분석한다.
- `--exclude rp-<ID>=<사유>`를 반복 지정하면 해당 공고를 분모에서 빼고 제외 사유를 집계에 남긴다.
- 직무별로 배치 하나를 실행한다. 기본 직무는 `cloud_engineer`이며 공고의 `role`이 대응하는 직무와 다르면 오류로 보고한다.
- 기술별로 확신도가 `high`인 고유 공고만 센다. 빈 기술 목록이나 `uncertain`만 있는 정상 추출도 전체 공고 수에는 포함한다.
- 일부 추출·S3·DB 저장이 실패하면 성공한 추출 결과는 보존하고 수요 집계 교체는 보류한다. 같은 명령으로 재시도하면 성공 결과를 재사용한다.
- 입력이 없거나 모든 공고가 제목 필터에서 탈락·제외되면 `published=false`로 종료한다. 빈 집계로 수요를 교체하지 않는다.
- 완료 시 공개 여부, 대상·성공·재사용·실패·제외 건수와 실패 호출을 포함한 토큰 합계를 JSON 한 줄로 출력한다.
- 앱의 실제 모드는 DynamoDB의 완성된 `demand`를 조회한다. 조회 캐시는 최대 60초 후 갱신된다.

필요한 IAM 권한은 수집 시 S3 `PutObject`, `GetObject`, DynamoDB `GetItem`, `PutItem`, `Query`,
Bedrock 모델 호출 권한이다. 테이블 생성 명령에는 DynamoDB `CreateTable`, `DescribeTable` 권한도 필요하다.
Query의 페이지 처리는 [AWS SDK 문서](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/table/query.html)를 따른다.

### 저장 경로 검증

```powershell
uv run --python 3.11 --with boto3 python -m unittest pipeline.test_storage pipeline.test_rocketpunch_storage
uv run --python 3.11 --with boto3 python -m pipeline.test_rocketpunch
uv run --python 3.11 --with boto3 python -m unittest ai.test_extract backend.test_dynamodb backend.test_demand pipeline.test_extraction_batch pipeline.test_rocketpunch_dynamodb
```

단위 테스트는 임시 파일과 모의 S3를 사용하며 실제 API나 AWS를 호출하지 않는다.

## 교육과정 검색 (`search_courses`)

부족한 기술 → AWS 교육과정 후보. 반환은 `[{course_id, subskill_id, match}]`이고 형식은 바뀌지 않았다.
규칙(별칭 사전 → 세부 능력 → 이름이 제목·학습 목표에 있는 과정)이 먼저이고, 자리가 남으면 키워드 점수와 임베딩 점수를 Reciprocal Rank Fusion으로 합쳐 채운다. 채운 과정은 `match: "uncertain"`이다.
`search_courses(skill, k, mode)`의 `mode`는 `rules`, `keyword`, `embedding`, `hybrid`, `final`(기본)이다. Reranking은 하지 않는다.

- 벡터를 쓸 수 없으면(가짜 모드, `data/courses/course_vectors.json` 없음, `EMBED_MODEL_ID`가 벡터를 만든 모델과 다름) `final`은 규칙 결과 그대로다.
- 과정 벡터 만들기(과정 본문을 바꾼 뒤에만): `BEDROCK_MOCK=0 python -X utf8 -m pipeline.build_course_vectors` (`--dry-run`은 호출 없이 건수만). 청크는 제목·학습 목표·설명 필드별이고 추천할 수 있는 과정 122개에서 366개다.
- 방식 비교표: `BEDROCK_MOCK=0 python -X utf8 -m pipeline.eval_search --detail final`. 정답은 `ai/eval/search_gold.json`(34개 기술, 초안이며 검토 전)이다.
- 키워드 기준(`KEYWORD_MIN`)과 의미 기준(`SEM_MIN`)은 그 34개 기술로 정한 값이라 기술이 늘면 다시 본다.

## 기준 단계 (#90)

`python -m pipeline.required_levels`는 공고 근거 문장과 보유 수준 단계 예시(`data/skill_levels.json`)를 임베딩으로 비교해 기술별 기준 단계를 `data/required_levels.json`에 씁니다. `BEDROCK_MOCK=1`은 `data/sample` 추출 결과와 가짜 벡터(점수에 의미 없음), `BEDROCK_MOCK=0`은 DynamoDB 추출 결과와 `EMBED_MODEL_ID`를 씁니다. 결과 파일에는 집계 숫자만 들어갑니다. 유사도 기준(0.3)과 최소 공고 수(3)는 팀 결정값입니다.

