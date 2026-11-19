"""보유 수준 5단계 진단과 Gap 반영 (2026-11-19, #90).

- 사용자는 세부 능력(pipeline/courses/queries.csv 의 C1-1 등)마다 0~5단계를 고른다. 예시 문장은 data/skill_levels.json.
- 공고 기술(Python, Docker …)의 내 단계 = 그 기술이 걸린 세부 능력 단계 중 가장 높은 값.
  연결은 세부 능력 별칭(pipeline.search.subskills_of)을 먼저 보고, 없으면 skill_levels.json 의 skill_subskills 표를 본다.
  표가 "ALL"(AWS 같은 공통 기술)이면 1단계 이상으로 고른 세부 능력 단계의 평균이다.
- 기술마다 기준 단계(required_level)는 공고 근거 문장과 단계 예시를 비교해 정한 값이다(pipeline/required_levels.py → data/required_levels.json).
  파일에 없거나 표본이 부족한 기술은 DEFAULT_REQUIRED_LEVEL 과 "uncertain" 이다.
- 충족도 = 필수 비중 × (내 단계 ≥ 기준 단계 ? 1 : 0) + 우대 비중 × 내 단계 / 5.
  필수는 기준만 넘으면 되고, 우대는 높을수록 좋다는 팀 결정이다(2026-11-19). 내 단계 ≥ 기준 단계면 "보유"로 본다.

- 직무별(2026-11-19): 진단표, 기술 → 세부 능력 표, 기준 단계 파일을 직무마다 둔다(backend.jobs.job_file).
  세부 능력 id 는 직무끼리 겹치지 않아(클라우드 C*, AI A*) 저장한 수준 하나에 두 직무의 진단을 함께 둔다.

수준은 사용자가 스스로 고른 값이고 실력을 검증한 것이 아니다. 계산은 전부 코드로 한다(원칙 2).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend.jobs import ANALYSIS_JOBS, DEFAULT_JOB, job_file

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEVELS = tuple(range(6))  # 0 해 본 적 없음, 1~5
MAX_LEVEL = 5
DEFAULT_REQUIRED_LEVEL = 3  # 공고 비교로 정하지 못한 기술의 기준 단계(팀 결정값, 근거 자료 없음)
ALL = "ALL"
# 부족 기술의 내 단계 → 먼저 보여줄 과정 난이도(data/courses/courses.csv 의 level 값)
INTERMEDIATE_FROM = 3


@lru_cache(maxsize=None)
def job_guide(job_category: str = DEFAULT_JOB) -> dict:
    """직무 하나의 진단표(data/skill_levels.json, AI 는 data/skill_levels_ai_engineer.json).

    세부 능력 id 는 직무끼리 겹치지 않는다(클라우드 C*, AI A*). 단계 이름은 클라우드 파일의 것을 함께 쓴다.
    """
    data = json.loads((DATA_DIR / job_file("skill_levels.json", job_category)).read_text(encoding="utf-8"))
    data.setdefault("level_names", _base_guide()["level_names"])
    return data


@lru_cache(maxsize=1)
def _base_guide() -> dict:
    return json.loads((DATA_DIR / "skill_levels.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def level_guide() -> dict:
    """모든 직무의 세부 능력을 합친 진단표. 저장한 수준({세부 능력 id: 단계})의 검사와 화면 표시에 쓴다.

    skill_subskills 는 클라우드 표다(예전 호출과 같음). 직무별 표는 job_guide(직무)["skill_subskills"].
    """
    base = _base_guide()
    subskills = {}
    for job in ANALYSIS_JOBS:
        subskills.update(job_guide(job)["subskills"])
    return {**base, "subskills": subskills}


def job_subskills(job_category: str = DEFAULT_JOB) -> dict[str, dict]:
    """그 직무 진단표의 세부 능력 {id: 행}."""
    return job_guide(job_category)["subskills"]


@lru_cache(maxsize=None)
def required_levels(job_category: str = DEFAULT_JOB) -> dict:
    """직무별 기준 단계 파일(data/required_levels.json, AI 는 required_levels_ai_engineer.json). 없으면 빈 표(모두 기본값)."""
    path = DATA_DIR / job_file("required_levels.json", job_category)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("skills", {})


def subskills_for(skill: str, job_category: str = DEFAULT_JOB) -> list[str] | str:
    """기술이 걸린 세부 능력 id 목록, 공통 기술이면 "ALL", 연결이 없으면 빈 목록.

    클라우드: 세부 능력 별칭(pipeline.search.subskills_of)을 먼저 보고, 없으면 진단표의 skill_subskills.
    다른 직무: 그 직무 진단표의 skill_subskills 에 클라우드 연결을 더한다. 같은 기술(Docker, Python 등)을
    클라우드에서 진단했으면 그 값도 함께 본다(높은 쪽, skill_level).
    """
    if job_category == DEFAULT_JOB:
        from pipeline.search import subskills_of

        linked = subskills_of(skill)
        if linked:
            return linked
    table = {k.lower(): v for k, v in job_guide(job_category)["skill_subskills"].items() if not k.startswith("_")}
    own = table.get(skill.lower(), [])
    if job_category == DEFAULT_JOB or own == ALL:
        return own
    cloud = subskills_for(skill, DEFAULT_JOB)
    return [*own, *(sid for sid in cloud if sid not in own)] if isinstance(cloud, list) else own


def required_level(skill: str, job_category: str = DEFAULT_JOB) -> tuple[int, str]:
    """(기준 단계, "certain" | "uncertain")."""
    row = required_levels(job_category).get(skill)
    if not row:
        return DEFAULT_REQUIRED_LEVEL, "uncertain"
    return int(row["required_level"]), row["confidence"]


def validate_levels(levels: dict) -> dict[str, int]:
    """{세부 능력 id: 0~5} 를 검사한다. 모르는 id 나 범위 밖 값은 오류(조용히 0 으로 치지 않는다).

    모든 직무의 세부 능력 id 를 받는다. 직무를 바꿔도 다른 직무에서 진단한 값을 지우지 않는다.
    """
    known = level_guide()["subskills"]
    out = {}
    for sid, value in levels.items():
        if sid not in known:
            raise ValueError(f"모르는 세부 능력 id 입니다: {sid!r}")
        if isinstance(value, bool) or not isinstance(value, int) or value not in LEVELS:
            raise ValueError(f"{sid} 의 단계가 {value!r} 입니다. 0~{MAX_LEVEL} 정수여야 합니다")
        out[sid] = value
    return out


def skill_level(levels: dict[str, int], skill: str, job_category: str = DEFAULT_JOB) -> float:
    """세부 능력 단계로 기술 하나의 내 단계를 계산한다. 공통 기술(ALL)은 그 직무 세부 능력 중 진단한 값의 평균."""
    linked = subskills_for(skill, job_category)
    if linked == ALL:
        mine = job_subskills(job_category)
        values = [v for sid, v in levels.items() if v and sid in mine]  # 0(해 본 적 없음)은 진단하지 않은 것과 같게 본다
        return round(sum(values) / len(values), 1) if values else 0
    return max((levels.get(sid, 0) for sid in linked), default=0)


def skill_levels(owned, skills: list[str], job_category: str = DEFAULT_JOB) -> dict[str, float]:
    """보유 입력을 {기술: 내 단계} 로 바꾼다.

    - {세부 능력 id: 단계}(새 형식): 기술마다 skill_level 로 계산한다.
    - 기술 이름 목록(예전 형식, 상담 채팅도 이 형식): 목록에 있는 기술은 5단계(완전 보유), 없는 기술은 0.
      그래서 예전 호출의 보유 판정과 영역 몫은 전과 같다.
    """
    if owned is None:
        owned = []
    if isinstance(owned, dict):
        levels = validate_levels(owned)
        return {skill: skill_level(levels, skill, job_category) for skill in skills}
    names = {s.lower() for s in owned}
    return {skill: (MAX_LEVEL if skill.lower() in names else 0) for skill in skills}


def satisfaction(level: float, required: int, required_count: int, preferred_count: int) -> float:
    """기술 하나의 충족도(0~1). 필수 비중은 기준을 넘으면 다 채우고, 우대 비중은 단계에 비례해 채운다."""
    total = required_count + preferred_count
    if not total:
        return 1.0 if level >= required else round(level / MAX_LEVEL, 2)
    req_share = required_count / total
    return round(req_share * (1.0 if level >= required else 0.0) + (1 - req_share) * level / MAX_LEVEL, 2)


def course_level_first(level: float) -> str:
    """부족 기술의 내 단계로 먼저 보여줄 과정 난이도. 0~2단계는 기초, 3단계 이상은 중급."""
    return "중급" if level >= INTERMEDIATE_FROM else "기초"


# 확실한 추천 과정을 마쳤다고 볼 때의 예상 단계(레이더 "마친 뒤", #91). 팀 결정값(2026-11-19)이며 근거 자료는 없다.
# 기초 과정은 2단계(안내를 보며 기본 작업), 중급 과정은 3단계(혼자 구성·구현). 그 밖의 난이도는 올리지 않는다
AFTER_COURSE_LEVEL = {"기초": 2, "중급": 3}


def level_after_courses(level: float, course_levels: list[str]) -> float:
    """지금 단계와 마친 과정들의 난이도로 예상 단계를 낸다. 지금 단계가 더 높으면 그대로 둔다."""
    return max([level, *(AFTER_COURSE_LEVEL.get(lv, 0) for lv in course_levels)])


def whole(x: float):
    """7.0 → 7. 화면과 예전 테스트가 정수를 그대로 받게 한다."""
    return int(x) if float(x).is_integer() else round(x, 1)
