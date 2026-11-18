"""검토 공고 기본 선택, 중복 제거와 명시적 --glob 입력을 검사한다."""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

with patch.dict(sys.modules, {"dotenv": None}):
    from ai.eval import run_raw


def test_run_raw_inputs():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw_dir = root / "data" / "raw"
        reviewed = raw_dir / "rocketpunch-2026-11-17" / "postings-reviewed"

        def write(folder, job_id, role):
            folder.mkdir(parents=True, exist_ok=True)
            posting = {"id": f"rp-{job_id}", "title": "개발자", "text": "플랫폼 개발", "role": role}
            (folder / f"rp-{job_id}.posting.json").write_text(json.dumps(posting), encoding="utf-8")

        write(reviewed / "ai", 1, "AI 엔지니어")
        write(reviewed / "cloud", 1, "클라우드 엔지니어")
        write(reviewed / "cloud", 2, "클라우드 엔지니어")
        write(raw_dir, 99, "클라우드 엔지니어")

        def run(*args):
            with tempfile.TemporaryDirectory(dir=root) as dest, patch.object(run_raw, "OUT_DIR", Path(dest)):
                with patch.object(sys, "argv", ["run_raw", *args]), contextlib.redirect_stdout(io.StringIO()):
                    assert run_raw.main() == 0
                result = json.loads(next(Path(dest).glob("raw_run_*.json")).read_text(encoding="utf-8"))
                return [row["id"] for row in result["rows"]]

        with patch.object(run_raw, "ROOT", root), patch.dict(os.environ, BEDROCK_MOCK="1", USAGE_LOG="off"):
            assert run() == ["rp-1", "rp-2"]
            assert run("--glob", str(raw_dir / "*.posting.json")) == ["rp-99"]
            assert run("--glob", str(reviewed / "*" / "*.posting.json"), "--limit", "2") == ["rp-1", "rp-2"]


if __name__ == "__main__":
    test_run_raw_inputs()
    print("통과")
