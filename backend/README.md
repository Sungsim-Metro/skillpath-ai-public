Lambda, DynamoDB, 집계·Gap 계산. (담당: 일현)

## 프로필 저장소

실제 모드는 `DYNAMODB_PROFILE_TABLE`의 문자열 파티션 키 `nickname`으로 한 사람의 프로필을 찾습니다. `profile`에는 직무·기술·경험·수준과 담은 교육과정(`saved_courses`)을 담고, 같은 항목의 `credential`에는 임의 솔트와 PBKDF2-HMAC-SHA256 검증값을 보관합니다. 화면과 상담에는 인증된 세션으로 읽은 프로필만 전달합니다. [프로필 데이터 예시](../data/sample/profile.json)

AWS 환경에서는 `DYNAMODB_PROFILE_TABLE`과 `AWS_REGION`을 앱과 같은 값으로 설정한 뒤 아래 명령으로 프로필 테이블을 준비합니다. 테이블이 이미 있으면 키와 온디맨드 설정을 확인합니다. 런타임에는 이 테이블의 `GetItem`, `UpdateItem` 권한이 필요합니다. 모의 모드에서는 첫 등록 시 로컬 SQLite 파일이 생성됩니다.

```bash
uv run --python 3.11 --with boto3 python -m backend.dynamodb create-profile-table
```

## 비밀번호 도입 전 프로필

같은 저장소를 사용하는 앱 인스턴스를 모두 새 버전으로 교체합니다. 구버전 앱은 닉네임만으로 데이터를 읽을 수 있습니다. 기존 닉네임의 소유권은 예전 저장 데이터만으로 확인할 수 없으므로, 운영자가 소유자를 별도로 확인한 뒤 앱과 같은 저장소에 연결된 터미널에서 아래 명령을 실행합니다. 닉네임과 새 비밀번호는 프롬프트에 입력합니다. 기존 프로필에 최초 비밀번호를 설정하며 직무·경험·수준을 보존합니다. 이미 비밀번호가 있는 계정은 이 방법으로 변경할 수 없습니다.

```bash
uv run --python 3.11 --with-requirements requirements.txt python -m backend.profile_admin enroll-legacy
```

로그인 시도는 서버 프로세스 안에서 닉네임별로 1분에 10회까지 허용하며 로그인에 성공하면 횟수를 초기화합니다.

## 회원 대화 기록 테이블 (`DYNAMODB_CHAT_TABLE`)

`backend/chat_history.py`가 로그인한 회원의 상담 대화를 DynamoDB 테이블 `skillpath-chat-history`에 세션별로 저장한다(`DYNAMODB_CHAT_TABLE`에 이 이름을 넣는다). 비회원은 저장하지 않는다. 저장 전에 이메일·전화번호(+82 국가번호 포함)·주민등록번호·외국인등록번호 모양은 가린다. 이름·주소는 가리지 못한다.

| 항목 | 값 |
| --- | --- |
| 파티션 키 | `nickname` (String) |
| 정렬 키 | `sk` (String) |
| 과금 | 온디맨드 |
| TTL 속성 | `expires_at` (Number, 기본 30일 뒤. `CHAT_HISTORY_TTL_DAYS`로 바꿈) |
| 앱 권한 | 이 테이블에 `PutItem`·`UpdateItem`·`Query` |

- 세션 줄 `sk = S#<세션ID>`: `title`(첫 질문 앞 30자), `updated_at`. 세션 목록은 `begins_with("S#")`로 조회한다.
- 턴 줄 `sk = T#<세션ID>#<UTC 시각>`: `prompt`, `answer`, `courses`(JSON 문자열). 한 세션의 대화는 `begins_with("T#<세션ID>#")`로 조회한다.
- 모의 모드(`BEDROCK_MOCK=1`)는 로컬 SQLite(`SKILLPATH_MOCK_CHAT_DB`)를 쓴다.

`DYNAMODB_CHAT_TABLE`과 `AWS_REGION`을 앱과 같은 값으로 설정한 뒤 아래 명령으로 테이블을 준비한다. 테이블이 없으면 만들고(온디맨드), 있으면 키와 요금 모드를 확인한다. `expires_at` TTL도 켠다(이미 켜져 있으면 속성 이름만 확인).
다른 테이블(공고·수요·프로필)과 같은 이름이면 거절한다.

```bash
uv run --python 3.11 --with boto3 python -m backend.dynamodb create-chat-table
```

한 턴 저장은 턴 줄 → 세션 줄 순서로 쓴다. 둘 사이에서 실패하면 그 턴은 남고 세션 줄만 늦게 생기며, 같은 세션의 다음 턴이 세션 줄을 만들어 목록이 복구된다.

저장하지 않는 턴: 비회원의 대화, 가드레일에 막힌 발화(`blocked`), 범위 밖 질문(`out_of_scope`), 모델 응답 검사 실패, 처리 오류. 화면에는 보이지만 DB에는 남지 않는다.

실제 모드에서 `DYNAMODB_CHAT_TABLE`이 없으면 이 기능은 **꺼진다**. 저장과 목록이 없고 화면도 패널과 "저장됩니다" 안내를 그리지 않는다(설정 없이 저장되는 것처럼 안내하는 일을 막는다).

개인정보는 두 겹으로 막는다. ① 입력 단계에서 Bedrock 가드레일이 이메일과 한국 휴대전화가 든 질문을 막고, 막힌 턴은 저장하지 않는다. ② 저장 전 마스킹이 가드레일이 보지 않는 모양(주민·외국인등록번호, 지역번호, `+82` 국가번호)과 가드레일을 부르지 않는 모의 모드를 덮는다.
둘 다 모양을 찾는 방식이라 이름과 주소는 가리지 못한다. 그래서 화면 안내가 개인정보를 입력하지 말라고 알린다. 이 서비스는 이메일·전화번호를 받는 입력칸이 없고(로그인은 닉네임과 비밀번호뿐), 자유 입력창에 사용자가 직접 적을 때만 들어올 수 있다.

### 배포 체크리스트 (대화 기록)

권한이 없으면 저장이 **조용히 실패**한다(상담은 계속되고 로그에 경고만 남는다). 배포 전후에 아래를 확인한다.

1. 앱 환경(`.env`)에 `DYNAMODB_CHAT_TABLE=skillpath-chat-history`가 있다. 이 변수는 새 코드를 배포하기 전에 넣어도 안전하다(옛 코드는 읽지 않는다).
2. 앱 실행 역할(IAM)이 이 테이블에 `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query`를 허용한다. 정책의 `Resource`가 `table/skillpath-*`처럼 이 테이블을 포함하는지 본다
   (`aws iam get-role-policy --role-name <앱 실행 역할> --policy-name <정책 이름>`).
3. 배포한 앱에서 회원으로 로그인해 질문을 한두 개 한다. 왼쪽 패널 **최근**에 제목이 뜨고, 아래 명령의 개수가 질문 1개당 2씩(세션 줄 + 턴 줄) 늘어난다.
   ```bash
   aws dynamodb scan --table-name skillpath-chat-history --select COUNT --query Count
   ```
4. 앱 로그에 `대화 기록을 저장하지 못했습니다(` 경고가 없다. `ClientError`가 보이면 2번 권한을, `ValueError`가 보이면 1번 설정을 먼저 본다.
5. TTL이 켜져 있다(`aws dynamodb describe-time-to-live --table-name skillpath-chat-history`에서 `ENABLED`, `expires_at`).

### 분석용 내보내기 (S3 `skillpath-ai-chat-export-<계정ID>`)

대화 기록의 **원본은 DynamoDB**(`skillpath-chat-history`)이고, 사용자 테스트처럼 한꺼번에 볼 때만 S3로 **복사본**을 내보낸다.
앱은 이 버킷을 읽거나 쓰지 않는다(앱 실행 역할의 S3 권한은 데이터 버킷뿐이다). 테이블을 되살리는 복구용 백업이 아니다.

| 항목 | 값 |
| --- | --- |
| 버킷 | `skillpath-ai-chat-export-<계정ID>` (ap-northeast-2, 계정 ID는 `aws sts get-caller-identity`로 확인) |
| 공개 | 모두 차단 (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`) |
| 암호화 | 기본 암호화(AES256) |
| 수명 | 만든 지 **14일** 뒤 자동 삭제(규칙 `expire-14d`), 끊긴 업로드는 1일 뒤 정리 |
| 버전 관리 | 끔 |

**보관 기간 규칙.** S3 사본의 수명은 사본을 만든 날부터 센다. 사용자에게 알린 "대화 후 30일"을 넘지 않으려면
**(대화한 날부터 내보낸 날까지의 일수) + 수명 ≤ 30** 이어야 한다. 테스트 직후에 내보내면 14일로 충분하다.
30일이 지나면 DynamoDB 원본도 사라져 다시 내보낼 수 없다. 그 뒤에도 남길 분석 결과는 대화 원문이 아니라 집계한 숫자와 요약으로 따로 뽑는다.

**내보내기**(사람이 실행한다. SSO 로그인 후, 읽는 권한은 `dynamodb:Scan`, 쓰는 권한은 이 버킷의 `s3:PutObject`):

```bash
export AWS_PROFILE=kosa AWS_REGION=ap-northeast-2 PYTHONIOENCODING=utf-8
ACCT=$(aws sts get-caller-identity --query Account --output text); BUCKET=skillpath-ai-chat-export-$ACCT
python -X utf8 - <<'PY' | aws s3 cp - "s3://$BUCKET/chat-history/export-$(date +%Y%m%d-%H%M).jsonl" --sse AES256
import boto3, json
t = boto3.resource("dynamodb", region_name="ap-northeast-2").Table("skillpath-chat-history")
kw = {}
while True:
    r = t.scan(**kw)
    for item in r["Items"]:
        print(json.dumps(item, ensure_ascii=False, default=str))
    if "LastEvaluatedKey" not in r:
        break
    kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}
PY
```

한 줄에 한 항목(JSONL)이다. 세션 줄(`sk`가 `S#…`)과 턴 줄(`sk`가 `T#…`)이 함께 들어 있다.
분석이 끝나면 14일을 기다리지 말고 바로 지운다: `aws s3 rm "s3://$BUCKET/chat-history/" --recursive`

**주의.**
- 대화 원문이다. 연락처·등록번호 모양은 가렸지만 이름과 주소는 가리지 못한다. 저장소, PR, 팀 채팅에 올리지 않고, 접근은 팀원으로 제한한다.
- 내보낸 파일을 로컬에 내려받아 두지 않는다(위처럼 읽은 자리에서 바로 올린다).
- 자동 실행(예약)은 하지 않는다. 앱 서버의 역할에 S3 쓰기 권한을 더해야 해서, 사람이 필요할 때만 실행한다.
- 분석에 쓴다는 사실이 사용자 안내(설문과 화면 안내문)에 들어 있는지 확인한다.

