"""직군별 시장 Gap과 원본 실습의 산업별 Gap을 같은 수요·공급 비중 공식으로 계산한다."""

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict

from ai import sections
from pipeline.aliases import find_skills as extract_skills

CSV_FIELDS = ("industry", "skill", "demand_pct", "supply_pct", "gap_pp")
JOB_CSV_FIELDS = ("job_category", *CSV_FIELDS[1:])
PROGRAM_TEXT_FIELDS = ("title", "description", "objectives")


class SkillSupply(TypedDict):
    skill: str
    courses: int


class EducationSupply(TypedDict):
    source: str
    total_courses: int
    skills: list[SkillSupply]
    note: NotRequired[str]


class SkillGap(TypedDict):
    skill: str
    demand_pct: float
    supply_pct: float
    gap_pp: float


class GapRow(SkillGap):
    industry: str


class JobGapRow(SkillGap):
    job_category: str


class RankedGap(TypedDict):
    skill: str
    gap_pp: float


class GapAnalysis(TypedDict):
    rows: list[GapRow]
    top3: dict[str, list[RankedGap]]


class JobGapAnalysis(TypedDict):
    rows: list[JobGapRow]
    top3: dict[str, list[RankedGap]]


def posting_weights(text: str) -> dict[str, float]:
    """공고 한 건에서 기술마다 가장 높은 구역 가중치를 구한다."""
    headings = {start: section for start, _, section in sections.headings(text)}
    # 원문에는 기술별 필수·우대 판정이 없어 미분류 칸에는 우대 가중치를 적용한다.
    current_weight: float | None = sections.weight("preferred", None)
    offset = 0
    weights: dict[str, float] = {}
    for line in text.splitlines(keepends=True):
        if offset in headings:
            section = headings[offset]
            current_weight = None if section == sections.EXCLUDED else sections.weight("preferred", section)
        elif current_weight is not None:
            for skill in extract_skills(line):
                weights[skill] = max(weights.get(skill, 0.0), current_weight)
        offset += len(line)
    return weights


def _shares(totals: Mapping[str, float], record_count: int) -> dict[str, float]:
    if record_count == 0:
        return {}
    rates = {skill: total / record_count for skill, total in totals.items()}
    # Python 3.12 changed float sum(). Preserve the reference's Python 3.11 rounding.
    rate_sum = 0.0
    for rate in rates.values():
        rate_sum += rate
    denominator = rate_sum or 1.0
    return {skill: rate / denominator for skill, rate in rates.items()}


def gap_table(demand: dict[str, float], supply: dict[str, float]) -> list[RankedGap]:
    """0~1 비중의 차이를 %p로 환산해 반올림한 뒤 산업 내 상위 세 기술을 구한다."""
    ranking: list[RankedGap] = []
    for skill in demand.keys() | supply.keys():
        gap = demand.get(skill, 0.0) - supply.get(skill, 0.0)
        ranking.append({"skill": skill, "gap_pp": round(gap * 100, 1)})
    return sorted(ranking, key=lambda row: row["gap_pp"], reverse=True)[:3]


def aggregate_supply(courses: Sequence[Mapping[str, str | None]], source: str) -> EducationSupply:
    """학습 프로그램 전체에서 각 기술을 다루는 과정 수를 세어 교육 공급 집계(⑤)를 만든다."""
    counts: Counter[str] = Counter()
    for program in courses:
        text = " ".join(program[field] or "" for field in PROGRAM_TEXT_FIELDS)
        counts.update(extract_skills(text))
    return {
        "source": source,
        "total_courses": len(courses),
        "skills": [{"skill": skill, "courses": count} for skill, count in counts.items()],
    }


def _analyze_groups(
    postings: Sequence[Mapping[str, str]], supply: EducationSupply, group_field: str
) -> tuple[dict[str, list[SkillGap]], dict[str, list[RankedGap]]]:
    posting_counts: Counter[str] = Counter()
    demand_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for posting in postings:
        group = posting[group_field]
        posting_counts[group] += 1
        for skill, weight in posting_weights(posting["text"]).items():
            demand_totals[group][skill] += weight

    counts = {row["skill"]: row["courses"] for row in supply["skills"]}
    supply_shares = _shares(counts, supply["total_courses"])

    groups: dict[str, list[SkillGap]] = {}
    top3: dict[str, list[RankedGap]] = {}
    for group, totals in demand_totals.items():
        demand = _shares(totals, posting_counts[group])
        top3[group] = gap_table(demand, supply_shares)
        groups[group] = []
        for skill in sorted(demand.keys() | supply_shares.keys()):
            demand_pct = demand.get(skill, 0.0) * 100
            supply_pct = supply_shares.get(skill, 0.0) * 100
            groups[group].append({
                "skill": skill,
                "demand_pct": round(demand_pct, 1),
                "supply_pct": round(supply_pct, 1),
                "gap_pp": round(demand_pct - supply_pct, 1),
            })
    return groups, top3


def analyze_job_gap_from_supply(
    postings: Sequence[Mapping[str, str]], supply: EducationSupply
) -> JobGapAnalysis:
    """Compare each job category's demand with the same learning program catalog."""
    groups, top3 = _analyze_groups(postings, supply, "job_category")
    rows = [JobGapRow(job_category=job, **row) for job, skills in groups.items() for row in skills]
    return {"rows": rows, "top3": top3}


def analyze_gap_from_supply(
    postings: Sequence[Mapping[str, str]], supply: EducationSupply
) -> GapAnalysis:
    """산업별 채용 수요와 교육 공급 집계의 비중을 비교해 시장 Gap과 상위 세 기술을 구한다."""
    groups, top3 = _analyze_groups(postings, supply, "industry")
    rows = [GapRow(industry=industry, **row) for industry, skills in groups.items() for row in skills]
    return {"rows": rows, "top3": top3}


def analyze_gap(
    postings: Sequence[Mapping[str, str]], courses: Sequence[Mapping[str, str | None]]
) -> GapAnalysis:
    """공고와 프로그램 원본에서 공급 집계와 산업별 시장 Gap을 계산한다."""
    return analyze_gap_from_supply(postings, aggregate_supply(courses, source="학습 프로그램 목록"))


def calculate_gap(
    postings: Sequence[Mapping[str, str]], courses: Sequence[Mapping[str, str | None]]
) -> list[GapRow]:
    """Skills Gap 명세의 대시보드 5열에 해당하는 행을 반환한다."""
    return analyze_gap(postings, courses)["rows"]


def _csv(rows: Sequence[Mapping[str, object]], fields: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def gap_csv(rows: Sequence[GapRow]) -> bytes:
    """빈 결과도 헤더를 갖는 UTF-8 BOM CSV로 직렬화한다."""
    return _csv(rows, CSV_FIELDS)


def job_gap_csv(rows: Sequence[JobGapRow]) -> bytes:
    """Export the job category alongside each skill and its market gap."""
    return _csv(rows, JOB_CSV_FIELDS)
