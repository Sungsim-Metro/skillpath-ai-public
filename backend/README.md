Lambda, DynamoDB, 집계·Gap 계산. (담당: 일현)

## 프로필 저장소

실제 모드는 `DYNAMODB_PROFILE_TABLE`의 문자열 파티션 키 `nickname`으로 한 사람의 프로필을 찾습니다. `profile`에는 직무·기술·경험·수준을 담고, 같은 항목의 `credential`에는 임의 솔트와 PBKDF2-HMAC-SHA256 검증값을 보관합니다. 화면과 상담에는 인증된 세션으로 읽은 프로필만 전달합니다. [프로필 데이터 예시](../data/sample/profile.json)

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
