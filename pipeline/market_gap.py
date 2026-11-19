"""Export learning program supply counts and market Skills Gap results by job category."""

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.industry_gap import (
    JOB_CSV_FIELDS,
    PROGRAM_TEXT_FIELDS,
    EducationSupply,
    JobGapRow,
    aggregate_supply,
    analyze_job_gap_from_supply,
    job_gap_csv,
)
from backend.jobs import JOBS, REVIEWED_DOMAINS, validate_role
from pipeline.reviewed_postings import SNAPSHOT, reviewed_paths

ROOT = Path(__file__).resolve().parents[1]
CLEAN = Path("outputs/postings_clean.json")
RAW = Path("data/raw/job_postings_raw.json")
REVIEWED = RAW.parent / SNAPSHOT / "postings-reviewed"
PROGRAM_CATALOGS = {"AWS Skill Builder": Path("data/courses/courses.csv")}
SUPPLY = Path("outputs/learning_program_supply.json")
TABLE = Path("outputs/learning_program_job_gap_table.csv")
REPORT_NAME = "learning_program_job_gap_report.md"


def _records(path: Path, keys: tuple[str, ...], fields: tuple[str, ...]) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"{path.name}에 {'.'.join(keys)} 필드가 필요합니다.")
        value = value[key]
    if not isinstance(value, list):
        raise ValueError(f"{path.name}의 레코드는 배열이어야 합니다.")  # noqa: TRY004 - invalid file contents
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in fields):
            raise ValueError(f"{path.name} {index}번째 레코드의 {', '.join(fields)} 필드를 확인하세요.")
    return value


def _clean_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def catalog_supply(root: Path) -> EducationSupply:
    """Count the programs in the registered catalogs without requiring job postings."""
    programs = []
    for provider, relative_path in PROGRAM_CATALOGS.items():
        path = root / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"{provider}의 학습 프로그램 파일 {relative_path}을 준비하세요.")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = [field for field in PROGRAM_TEXT_FIELDS if field not in (reader.fieldnames or ())]
            if missing:
                raise ValueError(f"{relative_path}에 {', '.join(missing)} 열이 필요합니다.")
            for row in reader:
                if None in row or any(row[field] is None for field in PROGRAM_TEXT_FIELDS):
                    raise ValueError(f"{relative_path} {reader.line_num}행의 열 수를 확인하세요.")
                programs.append({field: row[field] for field in PROGRAM_TEXT_FIELDS})
    return aggregate_supply(programs, source=", ".join(PROGRAM_CATALOGS))


def _read_supply(path: Path) -> EducationSupply:
    supply = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(supply, dict) or not isinstance(supply.get("source"), str):
        raise ValueError(f"{path.name}에 공급 출처가 필요합니다.")
    total = supply.get("total_courses")
    if type(total) is not int or total < 0 or not isinstance(supply.get("skills"), list):
        raise ValueError(f"{path.name}의 전체 과정 수와 기술 목록을 확인하세요.")
    seen = set()
    for row in supply["skills"]:
        if not isinstance(row, dict) or not isinstance(row.get("skill"), str):
            raise ValueError(f"{path.name}의 기술 이름을 확인하세요.")
        skill, count = row["skill"], row.get("courses")
        if not skill.strip() or skill in seen or type(count) is not int or not 0 <= count <= total:
            raise ValueError(f"{path.name}의 기술별 과정 수 또는 중복 기술을 확인하세요.")
        seen.add(skill)
    return supply


def dashboard_supply(root: Path, *, allow_example: bool) -> EducationSupply:
    """Read the published counts, or count the available catalogs on the first run."""
    if (root / SUPPLY).is_file():
        return _read_supply(root / SUPPLY)
    sample = root / "data/sample/supply.json"
    if allow_example and sample.is_file() and not any((root / path).exists() for path in PROGRAM_CATALOGS.values()):
        return _read_supply(sample)
    return catalog_supply(root)


def _labelled_postings(path: Path, *, raw: bool) -> list[dict[str, str]] | None:
    """Read job-labelled inputs. An industry-only file cannot supply job membership."""
    text_field = "jobCont" if raw else "text"
    rows = _records(path, ("response", "items") if raw else (), (text_field,))
    if rows and all("job_category" not in row and "role" not in row for row in rows):
        return None
    role_codes = {name: code for code, name in JOBS.items()}
    postings = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        job = row.get("job_category")
        if job is None and isinstance(row.get("role"), str):
            job = role_codes.get(row["role"])
        if not isinstance(job, str) or job not in JOBS:
            raise ValueError(f"{path.name} {index}번째 공고의 직무(job_category 또는 role)를 확인하세요.")
        if "role" in row:
            validate_role(job, row["role"])
        posting_id = row.get("id")
        if posting_id is not None:
            if not isinstance(posting_id, str) or not posting_id.strip():
                raise ValueError(f"{path.name} {index}번째 공고의 id를 확인하세요.")
            key = (job, posting_id)
            if key in seen:
                continue
            seen.add(key)
        text = row[text_field]
        postings.append({"job_category": job, "text": _clean_text(text) if raw else text})
    return postings


def _load_reviewed_postings(root: Path, jobs: tuple[str, ...]) -> list[dict[str, str]]:
    postings = []
    for job in jobs:
        for path in reviewed_paths(REVIEWED_DOMAINS[job], raw_dir=root / RAW.parent):
            row = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise ValueError(f"{path.name}의 text 필드를 확인하세요.")
            if "role" in row:
                validate_role(job, row["role"])
            postings.append({"job_category": job, "text": row["text"]})
    return postings


def _load_postings(root: Path, jobs: tuple[str, ...], *, example: bool) -> tuple[list[dict], str]:
    sources = (CLEAN, RAW) if not example else (Path("data/sample/market_postings.json"),)
    for name in sources:
        path = (ROOT if example else root) / name
        if not path.is_file():
            continue
        labelled = _labelled_postings(path, raw=name == RAW)
        if labelled is not None:
            postings = [row for row in labelled if row["job_category"] in jobs]
            source = "예시 공고" if example else str(name)
            break
    else:
        if (root / REVIEWED).exists() and not example:
            postings = _load_reviewed_postings(root, jobs)
            source = f"{REVIEWED} 직군별 검토본"
        elif any((root / name).is_file() for name in sources):
            raise ValueError(f"공고에 직무 코드(job_category)나 직무 이름(role)이 필요합니다: {sources}")
        else:
            raise FileNotFoundError(f"직군별 채용공고 파일 {CLEAN}, {RAW} 또는 검토 공고 폴더 {REVIEWED}을 준비하세요.")
    counts = Counter(row["job_category"] for row in postings)
    sizes = ", ".join(f"{JOBS[job]} {counts[job]}건" for job in jobs)
    return postings, f"{source} ({sizes})"


def _gap_source(posting_source: str, supply: EducationSupply) -> str:
    return f"{posting_source} · {supply['source']} (학습 프로그램 {supply['total_courses']}개)"


def _read_table(path: Path, job_category: str) -> list[JobGapRow]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(JOB_CSV_FIELDS):
            raise ValueError(f"{path.name}의 열은 {', '.join(JOB_CSV_FIELDS)} 순서여야 합니다.")
        rows = []
        for row in reader:
            try:
                demand, supply, gap = (float(row[field]) for field in JOB_CSV_FIELDS[2:])
                if None in row or not all(map(math.isfinite, (demand, supply, gap))):
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name} {reader.line_num}행의 열과 수치를 확인하세요.") from exc
            if row["job_category"] not in JOBS:
                raise ValueError(f"{path.name} {reader.line_num}행의 직무 코드를 확인하세요.")
            if row["job_category"] == job_category:
                rows.append(JobGapRow(job_category=job_category, skill=row["skill"],
                                      demand_pct=demand, supply_pct=supply, gap_pp=gap))
        return rows


def dashboard_gap(root: Path, job_category: str, *, allow_example: bool) -> dict:
    """Use the learning-program export or calculate from the configured catalogs."""
    if job_category not in JOBS:
        raise ValueError(f"지원하지 않는 직무 코드: {job_category}")
    if (root / TABLE).is_file():
        return {"rows": _read_table(root / TABLE, job_category),
                "source": f"{TABLE} · {JOBS[job_category]} · 등록된 공급 출처 {', '.join(PROGRAM_CATALOGS)}"}
    example = allow_example and not any((root / name).exists() for name in (CLEAN, RAW, REVIEWED))
    postings, source = _load_postings(root, (job_category,), example=example)
    supply = dashboard_supply(root, allow_example=allow_example)
    return {"rows": analyze_job_gap_from_supply(postings, supply)["rows"], "source": _gap_source(source, supply)}


def _publish(path: Path, content: bytes) -> None:
    # Readers should always see one complete version, even during a rebuild.
    with TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_bytes(content)
        temporary.replace(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="directory containing data/ and outputs/")
    parser.add_argument("--output-dir", type=Path, help="destination for the generated files")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--example", action="store_true", help="use example postings with the registered program catalogs")
    mode.add_argument("--supply-only", action="store_true", help="export program counts without job postings")
    args = parser.parse_args(argv)
    dest = args.output_dir or args.root / ("outputs/example" if args.example else "outputs")
    try:
        supply = catalog_supply(args.root)
        if not args.supply_only:
            postings, source = _load_postings(args.root, tuple(JOBS), example=args.example)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    dest.mkdir(parents=True, exist_ok=True)
    _publish(dest / SUPPLY.name, (json.dumps(supply, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"{dest / SUPPLY.name} (학습 프로그램 {supply['total_courses']}개)")
    if args.supply_only:
        return
    result = analyze_job_gap_from_supply(postings, supply)
    lines = ["# 직군별 Skills Gap Top 3", "", _gap_source(source, supply), ""]
    for job, ranking in result["top3"].items():
        lines.extend([f"## {JOBS[job]}", ""])
        lines.extend(f"{rank}. {row['skill']} {row['gap_pp']:+.1f}%p"
                     for rank, row in enumerate(ranking, start=1))
        lines.append("")
    _publish(dest / REPORT_NAME, "\n".join(lines).encode("utf-8"))
    _publish(dest / TABLE.name, job_gap_csv(result["rows"]))
    print(f"{dest / TABLE.name} ({len(result['rows'])}행)\n{dest / REPORT_NAME}")


if __name__ == "__main__":
    main()
