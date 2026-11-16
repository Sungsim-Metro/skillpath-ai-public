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

색과 글꼴은 저장소 루트의 `.streamlit/config.toml`에서 정합니다. 차트는 Altair로 그립니다.
