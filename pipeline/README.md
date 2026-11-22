공고·NCS·교육과정 수집, 전처리, 별칭 사전, 임베딩 검색. (담당: 종훈)

## 교육 공급 집계

다음 명령은 채용공고 없이 학습 프로그램 목록만 읽고 `outputs/learning_program_supply.json`에 출처, 전체 과정 수와 기술별 과정 수를 기록합니다.

```sh
uv run --python 3.11 python -m pipeline.market_gap --supply-only
```

현재 출처는 `data/courses/courses.csv`의 AWS Skill Builder 과정 전체입니다. 각 과정의 제목·설명·학습 목표에서 검출된 기술을 과정당 한 번씩 셉니다. 다른 공급처를 추가하려면 프로그램마다 한 행을 두고 `title`, `description`, `objectives` 열을 갖춘 CSV를 `pipeline/market_gap.py`의 `PROGRAM_CATALOGS`에 등록합니다. [AWS Skill Builder 과정 수집 범위](courses/README.md#수집-조건)

앱의 `get_supply()`는 로컬에 저장된 공급 JSON을 우선 읽고, 파일이 없으면 등록된 과정 목록에서 바로 집계합니다. 모의 모드에서 저장된 집계와 등록된 과정 목록이 모두 없을 때만 `data/sample/supply.json`을 읽습니다. 기본 명령과 `--supply-only`는 원본 과정 목록으로 집계를 다시 만듭니다. 프로젝트 S3 데이터 버킷의 게시 경로는 `skillpath/learning-programs/learning_program_supply.json`입니다.

## 직군별 시장 Gap

저장소 루트에서 다음 명령을 실행하면 교육 공급 집계 JSON을 갱신하고 AI 엔지니어와 클라우드 엔지니어 각각의 공고 수요와 비교합니다. 전체 결과는 `outputs/learning_program_job_gap_table.csv`에, 직군별 Gap 상위 3개 기술은 `outputs/learning_program_job_gap_report.md`에 저장됩니다.

```sh
uv run --python 3.11 python -m pipeline.market_gap
```

공고는 `outputs/postings_clean.json`, `data/raw/job_postings_raw.json`의 `response.items`, `data/raw/rocketpunch-2026-11-17/postings-reviewed/` 순서로 찾습니다. 정제본과 원본에는 직군 코드(`job_category`) 또는 직군 이름(`role`)이 필요합니다. 산업명만 기록한 과거 파일은 건너뜁니다. 검토본의 `ai/`와 `cloud/`는 각 직군의 모집단입니다. 두 직군에 속한 공고 ID는 각 직군에서 한 번씩 셉니다.

공급 비중(%)은 해당 기술의 과정 수를 모든 기술의 과정 수 합계로 나누고 100을 곱해 구합니다. 같은 디렉터리 구조를 가진 다른 자료를 읽으려면 `--root 경로`를 지정합니다.

대시보드의 **부족 역량 → 채용 시장 Gap**은 직군별 CSV에서 선택한 직군의 행을 표시합니다. 직군별 CSV가 없으면 해당 직군의 공고 원문과 교육 공급 집계로 계산합니다. 공고, 과정 목록, 별칭 사전이나 칸 분류 규칙이 바뀌면 기본 명령을 다시 실행해 공급 집계와 시장 Gap을 함께 갱신합니다. 모의 모드에서 Gap 결과와 공고 입력이 모두 없으면 직군이 표시된 예시 공고를 사용합니다. 아래 명령은 예시 결과를 `outputs/example/`에 저장합니다.

```sh
uv run --python 3.11 python -m pipeline.market_gap --example
```

프로젝트 S3 데이터 버킷의 `skillpath/learning-programs/aws-skill-builder/`에는 과정 목록(`courses.csv`), 세부 능력 연결표(`course_subskill.csv`), 기존 임베딩의 보관 사본(`course_vectors.json`)이 있습니다. 직군별 Gap 표와 보고서를 배포할 때는 새 파일명으로 `skillpath/market-gap/` 아래에 게시합니다. 위 계산 명령은 로컬 파일을 갱신하며, S3에는 별도로 업로드합니다.

## 분석에 쓰는 채용공고

분석 입력은 `data/raw/rocketpunch-2026-11-17/postings-reviewed/` 아래 두 폴더다. 수요 배치는 여기에 오픈 데이터셋의 원티드 공고 `data/raw/hf/postings-reviewed/<cloud|ai>/hf-wanted-<번호>.posting.json`을 더해 집계한다(2026-11-19). 이 파일은 `python -m pipeline.wanted_dataset <parquet 폴더>`가 데이터셋에서 후보를 거르고 모델의 직무 판정을 거쳐 만든다. 이 폴더가 없으면 배치는 수요가 조용히 줄지 않게 오류로 멈춘다. 파일은 S3 `skillpath/wanted-dataset/processed/postings/`의 객체를 바이트 그대로 받아 둔다. `cloud/`는 클라우드 엔지니어, `ai/`는 AI 엔지니어 공고다. 분류는 모델이 공고의 근거 문장을 확인한 결과이며, 해당 시점의 수량은 [데이터 출처](../docs/data-sources.md)에 기록한다.

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
- 기본 선정 버전은 `rocketpunch-reviewed-v1`이다. 입력에 원티드 공고가 있으면 뒤에 `+wanted-dataset-v1`이 붙는다. 독립 배치에 `--terms`를 지정하면 검토본 중 제목이 일치하는 공고로 범위를 좁히고 필터 해시를 버전에 반영한다. 수집기의 `--terms`는 수집 후보만 고른다.
- 같은 본문·프롬프트·모델·추출기·설정의 성공 결과는 재사용한다. `--force`를 추가하면 같은 입력도 다시 분석한다.
- `--exclude rp-<ID>=<사유>`(원티드는 `hf-wanted-<ID>=<사유>`)를 반복 지정하면 해당 공고를 분모에서 빼고 제외 사유를 집계에 남긴다.
- 직무별로 배치 하나를 실행한다. 기본 직무는 `cloud_engineer`이며 공고의 `role`이 대응하는 직무와 다르면 오류로 보고한다.
- 기술별로 확신도가 `high`인 고유 공고만 센다. 빈 기술 목록이나 `uncertain`만 있는 정상 추출도 전체 공고 수에는 포함한다.
- 일부 추출·S3·DB 저장이 실패하면 성공한 추출 결과는 보존하고 수요 집계 교체는 보류한다. 같은 명령으로 재시도하면 성공 결과를 재사용한다.
- 입력이 없거나 모든 공고가 제목 필터에서 탈락·제외되면 `published=false`로 종료한다. 빈 집계로 수요를 교체하지 않는다.
- 완료 시 공개 여부, 대상·성공·재사용·실패·제외 건수와 실패 호출을 포함한 토큰 합계를 JSON 한 줄로 출력한다.
- 앱의 실제 모드는 DynamoDB의 완성된 `demand`를 조회한다. 조회 캐시는 최대 60초 후 갱신된다.

CLI와 서버리스 배치는 새 추출 결과를 같은 테이블의 실행 전용 파티션(`run#<직무>#<실행 ID>`)에 적재한다. 집계에 성공하면 처음 읽은 `snapshot_id`가 그대로인 경우에만 수요와 `extraction_partition` 포인터를 함께 교체한다. `DynamoDBStore.get_extraction`과 `list_extractions`는 이 포인터를 따르고, 포인터가 없는 기존 집계에서는 직무 기본 키로 조회한다. 서버리스 배치의 S3 매니페스트 생성 및 검증 명령은 [AWS 배포 가이드](../infra/README.md)에 있다.

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
실제 모드에서는 `BEDROCK_KB_ID`가 가리키는 Bedrock Knowledge Base의 `Retrieve` API로 S3 Vectors를 검색한다. `type=program`이고 추천 조건을 만족하는 문서만 검색하고, 여러 청크가 같은 과정에 속하면 가장 높은 점수를 사용한다. 규칙으로 확인한 과정이 먼저이며, 보충 후보는 규칙·키워드·KB 순위를 결합해 `match: "uncertain"`으로 표시한다. AWS 입문 요청은 사람이 지정한 과정 ID로 KB를 검색한다.
`search_courses(skill, k, mode)`의 `mode`는 `rules`, `keyword`, `embedding`, `hybrid`, `final`(기본)이다. Reranking은 하지 않는다.

- 모의 모드(`BEDROCK_MOCK=1`)의 기본 검색은 AWS 호출 없이 규칙을 사용한다. 실제 모드(`BEDROCK_MOCK=0`)의 앱 자격 증명에는 해당 KB의 `bedrock:Retrieve` 권한이 필요하다. KB 서비스 역할이 임베딩 모델과 S3 Vectors에 접근한다.
- 방식 비교표: `BEDROCK_MOCK=0 python -X utf8 -m pipeline.eval_search --detail final`. 정답은 `ai/eval/search_gold.json`(52개 기술: 클라우드 34개 + AI 18개, 초안이며 검토 전)이다.

### 교육과정 문서 동기화

[CloudFormation 템플릿](learning-programs-kb.yaml)은 기존 일반 S3 버킷과 벡터 버킷을 받아 KB, 서비스 역할, 1,024차원 코사인 인덱스와 S3 데이터 소스를 만든다. 배포할 때 `DocumentBucket`, `VectorBucket`, `EmbeddingModelId`를 전달한다. 현재 임베딩 모델은 Titan Text Embeddings V2이며 문서는 300토큰 단위로 20% 겹치게 나눈다. 추천에 쓰는 과정 ID와 추천 가능 여부는 과정별 메타데이터에 담는다.

카탈로그 변경 후 아래 명령으로 현재 문서와 메타데이터를 동기화한다. 서울 리전의 프로젝트 AWS 프로필을 사용하고 `PROGRAM_BUCKET`에는 프로젝트 데이터 버킷 이름을 지정한다.

```sh
export AWS_PROFILE=kosa AWS_DEFAULT_REGION=ap-northeast-2
stack=skillpath-learning-programs-kb
program_dir=$(mktemp -d)
uv run --python 3.11 python -m pipeline.build_program_documents --catalog data/courses/catalog_all.csv --output "$program_dir"
aws s3 sync "$program_dir/" "s3://${PROGRAM_BUCKET:?}/skillpath/learning-programs/programs/"
kb=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue | [0]" --output text)
source_id=$(aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='DataSourceId'].OutputValue | [0]" --output text)
job=$(aws bedrock-agent start-ingestion-job --knowledge-base-id "$kb" --data-source-id "$source_id" --query ingestionJob.ingestionJobId --output text)
aws bedrock-agent get-ingestion-job --knowledge-base-id "$kb" --data-source-id "$source_id" --ingestion-job-id "$job" --query 'ingestionJob.[status,statistics]'
```

마지막 명령에서 `COMPLETE`와 실패 0건을 확인한다. 카탈로그에서 삭제한 과정은 해당 문서와 메타데이터 객체도 S3에서 제거해야 다음 동기화에 반영된다. 로컬 앱의 `.env`에는 스택 출력 `KnowledgeBaseId`를 `BEDROCK_KB_ID`로 설정한다.

2026-11-20 UTC에는 확장 목록 1,270개를 위 KB에 반영했다. 기존에만 있던 추천 불가 과정 2개를 보존해
총 1,272개 문서이며, 동기화 실패·삭제는 0건이다. 앱의 로컬 `data/courses/courses.csv`는 아직 216개로 유지했다.
기본 추천은 이 파일을 사용한다. 상담에서 난이도를 지정하면 같은 폴더의 `catalog_all.csv`도 조회해 해당 단계에서 후보를 고른다.

## 기준 단계 (#90)

`python -m pipeline.required_levels`는 공고 근거 문장과 보유 수준 단계 예시(`data/skill_levels.json`)를 임베딩으로 비교해 기술별 기준 단계를 `data/required_levels.json`에 씁니다. `BEDROCK_MOCK=1`은 `data/sample` 추출 결과와 가짜 벡터(점수에 의미 없음), `BEDROCK_MOCK=0`은 DynamoDB 추출 결과와 `EMBED_MODEL_ID`를 씁니다. 결과 파일에는 집계 숫자만 들어갑니다. 유사도 기준(0.3)과 최소 공고 수(3)는 팀 결정값입니다.

