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

프로필을 쓰려면 닉네임을 입력하고 “Docker 배포 경험을 프로필에 저장해줘”처럼 요청합니다. 프로젝트의 닉네임은 로그인 인증 기능이 없으므로 같은 닉네임은 같은 프로필을 엽니다. 대화 원문은 현재 Streamlit 세션에만 두며, 저장된 프로필에는 사용자가 지정한 내용만 반영됩니다.

상담 화면에는 이 대화에서 사용한 입력·출력 토큰과 합계, 익명 세션 ID가 표시됩니다. 여러 방식을 비교할 때는 방식마다 새 브라우저 탭에서 상담을 열고 같은 순서로 질문한 뒤 세션 ID를 기록하세요. 각 질문의 모델 호출, 재시도, 캐시에 없어서 새로 요청한 임베딩 토큰이 해당 대화의 누계에 포함됩니다. 모의 모드에서는 토큰이 0입니다.

관리자는 역량 대시보드의 `?view=dashboard&admin=<ADMIN_KEY 값>`에서 **운영 현황 → 대화 세션별** 표를 확인할 수 있습니다. 표는 이 서버의 사용량 로그에 기록된 실제 호출을 집계합니다. 기본 경로는 `outputs/usage/usage.jsonl`입니다. 세션 ID가 없는 예전 기록과 배치 호출은 전체 사용량에 포함됩니다. `USAGE_LOG=off`는 파일 기록을 끄며 현재 대화창의 누계는 계속 표시합니다.

| 모드 | 필요한 설정 |
| --- | --- |
| 모의 (`BEDROCK_MOCK=1`) | AWS 연결 없이 예시 대화를 실행합니다. 프로필은 기본적으로 `$XDG_DATA_HOME/skillpath-ai/chat-profiles.sqlite3`에 저장합니다. XDG 경로가 없으면 `~/.local/share`를 사용하며, `SKILLPATH_MOCK_PROFILE_DB`로 파일 위치를 지정할 수 있습니다. |
| 실제 (`BEDROCK_MOCK=0`) | 기존 모델·수요·과정 검색 설정과 함께 `BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`을 지정합니다. 런타임 역할에 해당 가드레일의 `bedrock:ApplyGuardrail` 권한이 필요합니다. 입력 점검에 실패한 메시지는 후속 분석에 전달하지 않습니다. |
| 실제 프로필 | `DYNAMODB_PROFILE_TABLE`에 문자열 파티션 키 `nickname`만 있는 테이블을 지정하고 런타임 역할에 해당 테이블의 `dynamodb:GetItem`, `dynamodb:UpdateItem`을 허용합니다. 저장을 요청할 때만 프로필을 갱신합니다. |

색과 글꼴은 저장소 루트의 `.streamlit/config.toml`에서 정합니다. 차트는 Altair로 그립니다.
