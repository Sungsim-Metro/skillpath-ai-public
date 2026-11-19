"""현직자 연결: 부족 영역에 맞는 개발자 모임 찾기와 사람 찾기 검색 링크 (2026-11-17 결정).

사람(개인) 정보를 수집·저장·표시하지 않는다. LinkedIn 등의 프로필을 자동으로 가져오지 않는다.
- 모임: 공개 페이지를 보고 골라 적은 data/communities.json 을 조회만 한다. 영역과 모임의 짝도 그 파일에 적혀 있다.
  영역 id 는 직무끼리 겹치지 않아(C1~C8, A1~A4) 한 파일에 두 직무의 짝을 함께 둔다.
- 검색 링크: 검색어가 든 주소만 만든다. 누르는 것도, 결과를 보는 것도 사용자다.
계산과 짝짓기는 전부 코드로 한다. LLM 을 쓰지 않는다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

DATA = Path(__file__).resolve().parent.parent / "data" / "communities.json"
LINKEDIN_PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="
MAX_TOPIC = 3  # 화면에 보여줄 주제 모임 수


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def suggest(area_rows: list[dict]) -> dict:
    """영역별 Gap(backend.api.get_area_gap 결과)에서 비어 있는 몫이 큰 영역 순으로 맞는 모임을 고른다.

    반환
      topic:   [{"community", "areas": [그 모임과 맞은 부족 영역 이름]}]  비어 있는 몫이 큰 영역에 맞는 모임부터, 최대 MAX_TOPIC 개
      general: 영역과 무관하게 보여줄 모임(입문·대학생)
      region:  지역 모임
      unmatched: 부족한데 맞는 모임을 확인하지 못한 영역 이름
      skipped: 맞는 모임은 있지만 자리가 모자라 보여 주지 못한 영역 이름
    공통·미분류처럼 영역 id 가 모임과 짝지어지지 않는 것은 unmatched 에 넣지 않는다(모임 주제로 볼 수 없는 묶음이다).
    """
    data = load()
    short = sorted(
        (r for r in area_rows if r["demand"] > r["owned"] and r["area_id"] not in ("COMMON", "UNCLASSIFIED")),
        key=lambda r: r["demand"] - r["owned"], reverse=True,
    )
    topics = [c for c in data["communities"] if c["kind"] == "topic"]
    hits_of = {row["area"]: [c for c in topics if row["area_id"] in c["areas"]] for row in short}
    unmatched = [row["area"] for row in short if not hits_of[row["area"]]]
    topic, used = [], {}

    def take(area: str, c: dict) -> bool:
        if c["id"] in used:
            if area not in used[c["id"]]["areas"]:
                used[c["id"]]["areas"].append(area)
            return True
        if len(topic) < MAX_TOPIC:
            used[c["id"]] = {"community": c, "areas": [area]}
            topic.append(used[c["id"]])
            return True
        return False

    # 먼저 부족이 큰 영역부터 영역마다 모임 하나씩. 한 영역의 모임 여러 개가 자리를 다 차지해 다른 영역이 조용히 빠지지 않게 한다
    covered = set()
    for row in short:
        if any(take(row["area"], c) for c in hits_of[row["area"]][:1]):
            covered.add(row["area"])
    for row in short:  # 남는 자리는 같은 순서로 나머지 모임을 채운다
        for c in hits_of[row["area"]][1:]:
            take(row["area"], c)
    skipped = [row["area"] for row in short if hits_of[row["area"]] and row["area"] not in covered]
    return {
        "topic": topic,
        "general": [c for c in data["communities"] if c["kind"] == "general"],
        "region": [c for c in data["communities"] if c["kind"] == "region"],
        "unmatched": unmatched,
        "skipped": skipped,
        "meta": {k: data[k] for k in ("source", "checked", "limits", "join")},
    }


def people_search_links(job_title: str, skills: list[str], limit: int = 3) -> list[dict]:
    """사람 찾기 검색 링크. 검색어가 든 LinkedIn 검색 주소만 만든다(조회하지 않는다).

    첫 링크는 직무 이름만으로 찾는 주소, 그 뒤는 직무와 부족 기술을 함께 넣은 주소다. 모두 합쳐 limit 개까지.
    """
    links = [{"label": f"LinkedIn에서 '{job_title}' 현직자 찾기", "url": LINKEDIN_PEOPLE_SEARCH + quote(job_title)}]
    links += [
        {"label": f"'{job_title} {skill}' 검색", "url": LINKEDIN_PEOPLE_SEARCH + quote(f"{job_title} {skill}")}
        for skill in skills[:limit - 1]
    ]
    return links
