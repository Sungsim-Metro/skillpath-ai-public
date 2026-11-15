"""#21 채점 준비(make_gold_template)와 1회 채점(score --split test --final) 의 잠금 확인.

실행: python -m ai.test_make_gold_template
임시 폴더에 가짜 공고를 만들어 쓴다. data/raw/ 의 실제 채점용 공고는 열지 않고, Bedrock 도 부르지 않는다
(추출 함수는 가짜로 바꿔 끼운다).
"""
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["BEDROCK_MOCK"] = "1"  # score 를 불러오기 전에. .env 는 이미 있는 값을 덮어쓰지 않는다

from ai.bedrock import ConverseResult  # noqa: E402
from ai.eval import make_gold_template as mgt  # noqa: E402
from ai.eval import score  # noqa: E402

TEXT_A = "[자격요건]\nDocker 로 서비스를 배포해 본 경험\n[우대사항]\nPython 개발 경험"
TEXT_B = "[주요업무]\nAWS 인프라 운영"


def run(fn, *args):
    """(돌려준 값, 화면 출력)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(*args)
    return code, buf.getvalue()


def write_posting(folder: Path, pid: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pid}.posting.json").write_text(json.dumps({"id": pid, "text": text}, ensure_ascii=False), encoding="utf-8")


def setup(tmp: Path) -> SimpleNamespace:
    """채점용 공고 2건과 확인표를 만들고(init), 정답지는 쓰지 않은 상태로 돌려준다."""
    d = SimpleNamespace(
        test_dir=tmp / "eval_test", postings=tmp / "eval_test" / "postings",
        gold=tmp / "eval_test" / "gold_test.json", manifest=tmp / "test_manifest.json",
        dev=tmp / "dev.json", log=tmp / "test_runs.log",
    )
    write_posting(d.postings, "t-001", TEXT_A)
    write_posting(d.postings, "t-002", TEXT_B)
    d.dev.write_text(json.dumps([{"id": "dev-1", "text": "개선용 공고"}], ensure_ascii=False), encoding="utf-8")
    code, out = run(mgt.init, d.postings, d.gold, d.manifest, [d.dev])
    assert code == 0, out
    return d


def write_gold(d: SimpleNamespace, skills_a: list[dict]) -> None:
    d.gold.write_text(json.dumps([{"posting_id": "t-001", "skills": skills_a}, {"posting_id": "t-002", "skills": []}],
                                 ensure_ascii=False), encoding="utf-8")


DOCKER = {"raw": "Docker", "normalized": "Docker", "type": "required", "evidence": "Docker 로 서비스를 배포해 본 경험"}


def final_args(d: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(final=True, prompt=None, runs=None, test_dir=d.test_dir, manifest=d.manifest, log=d.log)


def fake_extract(posting, prompt_version):
    return {"posting_id": posting["id"], "skills": []}, ConverseResult({"skills": []}, 1, {}), []


def expired(posting, prompt_version):
    raise RuntimeError("ExpiredTokenException")  # SSO 만료를 흉내 낸다


def test_init():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # 개선용 공고 id 가 섞이면 멈춘다
        postings = tmp / "p"
        write_posting(postings, "dev-1", "본문이 달라도 id 가 같으면 멈춘다")
        write_posting(postings, "t-001", TEXT_A)
        dev = tmp / "dev.json"
        dev.write_text(json.dumps([{"id": "dev-1", "text": "개선용 공고"}], ensure_ascii=False), encoding="utf-8")
        code, out = run(mgt.init, postings, tmp / "gold.json", tmp / "m.json", [dev])
        assert code == 1 and "dev-1" in out, out
        assert not (tmp / "gold.json").exists() and not (tmp / "m.json").exists()

    with tempfile.TemporaryDirectory() as t:
        d = setup(Path(t))
        # 정답지가 없어도 확인표가 있으면 덮어쓰지 않는다
        before = d.manifest.read_text(encoding="utf-8")
        d.gold.unlink()
        write_posting(d.postings, "t-003", "새로 끼운 공고")
        code, out = run(mgt.init, d.postings, d.gold, d.manifest, [d.dev])
        assert code == 1 and "확인표가 이미 있어" in out, out
        assert d.manifest.read_text(encoding="utf-8") == before and not d.gold.exists()


def test_check():
    with tempfile.TemporaryDirectory() as t:
        d = setup(Path(t))
        write_gold(d, [DOCKER])
        assert run(mgt.check, d.postings, d.gold, d.manifest, False)[0] == 0  # 맞는 정답지는 통과

        write_gold(d, [DOCKER, dict(DOCKER, type="preferred")])
        code, out = run(mgt.check, d.postings, d.gold, d.manifest, False)
        assert code == 1 and "같은 기술이 두 번" in out, out

        write_gold(d, [dict(DOCKER, evidence="Docker 운영 5년 이상")])
        code, out = run(mgt.check, d.postings, d.gold, d.manifest, False)
        assert code == 1 and "근거 문장이 공고 본문에 그대로 없습니다" in out, out

        write_gold(d, [DOCKER])
        write_posting(d.postings, "t-002", TEXT_B + "\n(수정됨)")  # 본문이 바뀌면 멈춘다
        code, out = run(mgt.check, d.postings, d.gold, d.manifest, False)
        assert code == 1 and "t-002" in out, out

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        write_posting(tmp / "p", "t-001", TEXT_A)
        code, out = run(mgt.check, tmp / "p", tmp / "gold.json", tmp / "m.json", False)
        assert code == 1 and "먼저 init" in out, out


def test_final():
    saved = score.OUT_DIR, score.extract_with_details
    try:
        with tempfile.TemporaryDirectory() as t:
            d = setup(Path(t))
            score.OUT_DIR = Path(t) / "out"  # 결과 파일도 임시 폴더에
            write_gold(d, [DOCKER])
            assert run(mgt.check, d.postings, d.gold, d.manifest, True)[0] == 0  # --fill

            # 확인표가 없으면 FileNotFoundError 대신 안내
            code, out = run(score.test_final, SimpleNamespace(**{**vars(final_args(d)), "manifest": Path(t) / "없음.json"}))
            assert code == 1 and "먼저 init" in out, out

            # 시작·중단 줄만 있으면 막지 않는다. 중단 때는 점수를 화면·파일에 남기지 않는다
            d.log.write_text("2026-11-18 10:00:00 시작 prompt=extract_v2_1 mode=bedrock\n"
                             "2026-11-18 10:01:00 중단 mode=bedrock 이유=ExpiredTokenException\n", encoding="utf-8")
            score.extract_with_details = expired
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    score.test_final(final_args(d))
                raise AssertionError("중단 오류가 그대로 드러나야 합니다")
            except RuntimeError:
                pass
            assert "F1" not in buf.getvalue() and "P " not in buf.getvalue(), buf.getvalue()
            assert not score.OUT_DIR.exists() or not list(score.OUT_DIR.iterdir())
            assert d.log.read_text(encoding="utf-8").splitlines()[-1].endswith("중단 mode=mock 이유=RuntimeError")

            # 다시 돌리면 끝까지 간다 (가짜 모드라 '완료 mode=mock' 이 남는다)
            score.extract_with_details = fake_extract
            code, out = run(score.test_final, final_args(d))
            assert code == 0 and "F1" in out, out
            assert "완료 mode=mock" in d.log.read_text(encoding="utf-8")

            # 가짜 모드 완료는 막지 않는다
            assert run(score.test_final, final_args(d))[0] == 0

            # 공고 본문이 바뀌면 멈춘다
            write_posting(d.postings, "t-002", TEXT_B + "\n(수정됨)")
            code, out = run(score.test_final, final_args(d))
            assert code == 1 and "확인표와 다릅니다" in out, out
            write_posting(d.postings, "t-002", TEXT_B)

            # '완료 mode=bedrock' 이 있으면 멈춘다 (점수를 이미 봤다)
            with d.log.open("a", encoding="utf-8") as f:
                f.write("2026-11-18 11:00:00 완료 mode=bedrock P=0.9 R=0.8 F1=0.85 오류=0 결과=x.json\n")
            code, out = run(score.test_final, final_args(d))
            assert code == 1 and "이미 실제 채점을 끝낸 기록" in out, out
    finally:
        score.OUT_DIR, score.extract_with_details = saved

    # 줄 판정: 완료이면서 mode=bedrock 인 줄만
    assert score.is_real_done("2026-11-18 11:00:00 완료 mode=bedrock P=0.9")
    assert not score.is_real_done("2026-11-18 11:00:00 완료 mode=mock P=0.9")
    assert not score.is_real_done("2026-11-18 11:00:00 중단 mode=bedrock 이유=완료")
    assert not score.is_real_done("2026-11-18 11:00:00 시작 prompt=extract_v2_1 mode=bedrock")


if __name__ == "__main__":
    test_init()
    test_check()
    test_final()
    print("통과")
