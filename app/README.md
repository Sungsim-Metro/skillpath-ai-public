Streamlit 화면. 화면 코드만 두고 로직은 다른 폴더의 함수를 호출합니다. (담당: 형섭)

| 파일 | 내용 |
| --- | --- |
| `main.py` | 사이드바 입력과 탭 배치. 실행은 저장소 루트에서 `streamlit run app/main.py` |
| `common.py` | 함께 쓰는 상수(색, 문구)와 도우미(`load`, `notice`), 탭에 넘기는 입력 묶음 `Ctx` |
| `tabs/demand.py` | 탭 1 시장 수요 |
| `tabs/gap.py` | 탭 2 부족 역량 |
| `tabs/recommend.py` | 탭 3 학습 추천 |
| `tabs/extract.py` | 탭 4 공고 직접 분석 |
| `test_app.py` | 가짜 모드에서 화면이 오류 없이 뜨는지 확인. `python -m app.test_app` |

새 탭은 `tabs/`에 `render(ctx)` 함수가 있는 파일을 더하고 `main.py`의 `TABS`에 한 줄을 넣습니다.
탭 파일끼리는 서로 가져다 쓰지 않습니다. 함께 쓸 것은 `common.py`에 둡니다.

## 학습 상담 실행

```bash
BEDROCK_MOCK=1 uv run --python 3.11 --with-requirements requirements.txt streamlit run app/main.py
```

사이드바에서 **학습 상담**을 선택하거나 `http://localhost:8501/?view=chat`으로 바로 접속합니다. “제가 Docker로 API를 배포했어요” 다음에 “내 경험을 정리해줘”, “무엇이 부족해?”, “그중 어떤 과정을 들을까?”를 차례로 입력하면 각 요청의 결과를 확인할 수 있습니다.

사이드바에서 닉네임과 비밀번호를 입력하고 **새 프로필 만들기**를 누릅니다. 다시 접속하면 같은 정보로 **로그인**합니다. 상담에서 “Docker 배포 경험을 프로필에 저장해줘”처럼 저장 대상을 지정할 수 있습니다. 대화 원문은 현재 Streamlit 세션에만 두며 **로그아웃**하면 대화와 개인 입력을 비웁니다. 손님도 현재 세션에서 진단과 상담을 이용할 수 있습니다.

보유 수준은 두 곳에서 입력합니다. **수준 진단하기**에서는 직무별 세부 능력의 예시를 보고 0~5단계를 선택합니다. 목록에 없는 기술은 **기술 수준 직접 입력**에서 이름과 단계를 입력하고 **기술 수준 저장**을 누릅니다. 예를 들어 `OpenTelemetry`를 4단계로 저장한 뒤 같은 이름을 3단계로 다시 저장하면 기존 항목이 갱신됩니다. 로그인하면 프로필에 저장한 단계가 복원됩니다. 상담에는 기존 설문에서 기준 단계를 충족한 기술을 전달합니다.

손님으로 먼저 입력한 뒤 로그인했을 때 계정에 저장한 수준이 비어 있으면 **손님 수준을 내 프로필에 저장**을 눌러 이어갈 수 있습니다. 이미 수준을 저장한 계정으로 로그인하면 그 계정의 값을 불러옵니다.

상담에서 “제가 OpenTofu를 사용했어요. OpenTofu만 프로필에 저장해줘”라고 하면 목표 직무를 바꿔도 그 기술이 프로필에 한 번 남습니다. 사전에 없는 이름도 사용할 수 있습니다. 모의 상담에서 한국어 자유 이름을 명확히 지정하려면 “저는 ‘데이터 품질 검사’를 사용했어요”처럼 이름을 따옴표로 감싸세요. 사용 경험만 밝힌 기술은 숙련도를 따로 입력할 때까지 이름만 저장합니다.

상담 화면에는 이 대화에서 사용한 입력·출력 토큰과 합계, 익명 세션 ID가 표시됩니다. 여러 방식을 비교할 때는 방식마다 새 브라우저 탭에서 상담을 열고 같은 순서로 질문한 뒤 세션 ID를 기록하세요. 각 질문의 모델 호출, 재시도, 캐시에 없어서 새로 요청한 임베딩 토큰이 해당 대화의 누계에 포함됩니다. 모의 모드에서는 토큰이 0입니다.

관리자는 `/admin?admin=<ADMIN_KEY 값>`(관리자 화면, `app/admin.py`)의 **토큰·비용 → 대화 세션별** 표를 확인할 수 있습니다. 같은 화면에 비교 실험 결과(모델별 평가·비용과 쓸 모델의 결정 기록, 추천 구조 A/B), 데이터 현황, 과정 커버리지, 평가 결과가 있습니다. 값이 없거나 틀리면 `/admin` 주소는 열리지 않습니다. 표는 이 서버의 사용량 로그에 기록된 실제 호출을 집계합니다. 기본 경로는 `outputs/usage/usage.jsonl`입니다. 세션 ID가 없는 예전 기록과 배치 호출은 전체 사용량에 포함됩니다. `USAGE_LOG=off`는 파일 기록을 끄며 현재 대화창의 누계는 계속 표시합니다.

| 모드 | 필요한 설정 |
| --- | --- |
| 모의 (`BEDROCK_MOCK=1`) | AWS 연결 없이 예시 대화를 실행합니다. 입력 검사는 `ai/guardrail.py`의 코드 검사만 합니다. 프로필은 기본적으로 `$XDG_DATA_HOME/skillpath-ai/chat-profiles.sqlite3`에 저장합니다. XDG 경로가 없으면 `~/.local/share`를 사용하며, `SKILLPATH_MOCK_PROFILE_DB`로 파일 위치를 지정할 수 있습니다. |
| 실제 (`BEDROCK_MOCK=0`) | 기존 모델·수요·과정 검색 설정과 함께 입력 가드레일의 `GUARDRAIL_ID`, `GUARDRAIL_VERSION`을 지정합니다(비어 있으면 예전 이름 `BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION` 묶음을 읽고, 두 묶음을 섞지 않습니다). 입력 검사는 상담 에이전트와 같은 `ai/guardrail.py`를 씁니다. 런타임 역할에 해당 가드레일의 `bedrock:ApplyGuardrail` 권한이 필요합니다. 입력 점검에 실패한 메시지는 후속 분석에 전달하지 않습니다. |
| 실제 프로필 | `DYNAMODB_PROFILE_TABLE`에 문자열 파티션 키 `nickname`만 있는 테이블을 지정하고 런타임 역할에 해당 테이블의 `dynamodb:GetItem`, `dynamodb:UpdateItem`을 허용합니다. [테이블 준비와 이전 프로필 전환](../backend/README.md) |
| 실제 과정 검색 | `BEDROCK_KB_ID`에 교육과정용 Knowledge Base ID를 지정합니다. 앱의 런타임 역할에는 해당 KB의 `bedrock:Retrieve` 권한이 필요합니다. |
| 비교 실험 | `EXPERIMENT_MODELS`에 모델 ID를 쉼표로 적으면 메시지마다 무작위로 한 모델을 쓰고 답 아래에서 평가를 받습니다. `AB_STRUCTURES`에 추천 구조 이름(`single,reviewed`)을 적으면 과정 추천을 A/B로 보여 주고 선택을 받습니다. 기록은 `DYNAMODB_FEEDBACK_TABLE`(파티션 키 `session_id`, 정렬 키 `turn_id`)에 남고 런타임 역할에 `dynamodb:UpdateItem`, `dynamodb:Scan` 권한이 필요합니다. 내용은 저장하지 않습니다. 셋 다 비우면 꺼집니다. |

색과 글꼴은 저장소 루트의 `.streamlit/config.toml`에서 정합니다. 차트는 Altair로 그립니다.
