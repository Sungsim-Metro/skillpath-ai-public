"""주제와 난이도 제한 없이 Skill Builder의 공개 과정 목록을 수집한다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://skillbuilder.aws/graphql"
PAGE_SIZE = 50
RESULT_WINDOW = 100
PARTITION_KEYS = ("courseLevel", "duration", "domain", "roles", "learningStyles", "catalogId")
LEVELS = {"fundamental": "기초", "intermediate": "중급", "advanced": "고급"}
STAR_KEYS = ("oneStar", "twoStar", "threeStar", "fourStar", "fiveStar")
USER_AGENT = "SkillPath-Catalog-Research/1.0"
log = logging.getLogger(__name__)

QUERY = """
query PublicCourseCatalog($input: SearchInputV2!) {
  searchV2(input: $input) {
    totalResults
    facets {
      key
      values { value { id value } totalResults }
    }
    results {
      item {
        id rootId name languageCode trainingObjectUrl description status
        objectives { values }
        intendedAudience { values }
        prerequisites { value }
        duration { unit value }
        skillLevel { value valueDisplayString }
        domain { value valueDisplayString }
        roles { valuesDisplayStrings }
        awsServices { valuesDisplayStrings }
        type { value valueDisplayString }
        accessTier { value valueDisplayString }
        expirationDate { value }
        auditMetadata { lastUpdatedAt activatedAt }
        reviewInsights {
          averageRating reviewCount
          ratingHistogram {
            oneStar { count } twoStar { count } threeStar { count }
            fourStar { count } fiveStar { count }
          }
        }
      }
    }
  }
}
"""

Filters = tuple[tuple[str, str], ...]


class PublicCatalogClient:
    def __init__(self, request_gap: float = 2.0):
        if request_gap < 2:
            raise ValueError("The minimum request interval is two seconds")
        self.request_gap = request_gap
        self.last_request = 0.0
        self.requests = 0

    def __call__(self, filters: Filters, page: int, sort: str | None = None) -> dict:
        payload = {
            "query": QUERY,
            "variables": {"input": {
                "language": "en-US", "pageNumber": page, "pageSize": PAGE_SIZE,
                "filters": [
                    {"key": "accessTier", "values": ["free"]},
                    {"key": "typeId", "values": ["digital_course"]},
                    *({"key": key, "values": [value]} for key, value in filters),
                ],
            }},
        }
        if sort is not None:
            criterion, _, order = sort.partition(":")
            payload["variables"]["input"].update(sortCriterion=criterion if order else "NAME",
                                                  orderBy=order or sort)
        for attempt in range(4):
            time.sleep(max(0.0, self.last_request + self.request_gap - time.monotonic()))
            self.last_request = time.monotonic()
            self.requests += 1
            request = Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST", headers={
                "Content-Type": "application/json", "Accept-Language": "en-US", "User-Agent": USER_AGENT,
            })
            try:
                with urlopen(request, timeout=45) as response:
                    data = json.load(response)
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                retry_after = error.headers.get("Retry-After", "")
                delay = 0.0
                if retry_after.isdigit():
                    delay = float(retry_after)
                elif retry_after:
                    try:
                        delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        pass
                time.sleep(max(2 ** (attempt + 2), delay))
                continue
            except (URLError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 2))
                continue
            if data.get("errors"):
                raise ValueError(f"Public catalog query failed: {data['errors']}")
            result = (data.get("data") or {}).get("searchV2")
            if not isinstance(result, dict):
                raise ValueError("The public catalog response has no searchV2 object")
            return result
        raise RuntimeError("The public catalog could not be read")


class CatalogCollector:
    def __init__(self, cache: Path, *, fetch: Callable[[Filters, int, str | None], dict] | None = None):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fetch = fetch if fetch is not None else PublicCatalogClient()
        self.items: dict[str, dict] = {}
        self.partitions: list[dict] = []
        self.issues: list[str] = []
        self.cache_hits = 0
        self.fetched_pages = 0

    def _page(self, filters: Filters, page: int, sort: str | None = None) -> dict:
        identity = [QUERY, filters, page] if sort is None else [QUERY, filters, page, sort]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            result = saved["response"]
            self.cache_hits += 1
        else:
            result = self.fetch(filters, page, sort)
            saved = {"collected_at": datetime.now(timezone.utc).isoformat(),
                     "filters": filters, "page": page, "sort": sort, "response": result}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            self.fetched_pages += 1
        total = result.get("totalResults")
        if type(total) is not int or total < 0 or not isinstance(result.get("results"), list):
            raise ValueError("The public catalog count or result list is invalid")
        for record in result["results"]:
            item = dict(record["item"])
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("A catalog item has no variant ID")
            item["_collected_at"] = saved["collected_at"]
            self.items[identifier] = item
        return result

    def _window(self, filters: Filters, sort: str | None = None) -> tuple[dict, set[str]]:
        first = self._page(filters, 1, sort)
        expected = first["totalResults"]
        found = {record["item"]["id"] for record in first["results"]}
        for page in range(2, math.ceil(min(expected, RESULT_WINDOW) / PAGE_SIZE) + 1):
            result = self._page(filters, page, sort)
            if result["totalResults"] != expected:
                self.issues.append(f"Count changed while paging {filters}")
            found.update(record["item"]["id"] for record in result["results"])
        return first, found

    def _walk(self, filters: Filters, used: frozenset[str]) -> set[str]:
        first, found = self._window(filters)
        expected = first["totalResults"]
        sorted_windows_read = False
        if len(found) < expected <= RESULT_WINDOW * 2:
            for sort in ("ASCENDING", "DESCENDING"):
                ordered, identifiers = self._window(filters, sort)
                if ordered["totalResults"] != expected:
                    self.issues.append(f"Count changed while sorting {filters}")
                found.update(identifiers)
                if len(found) >= expected:
                    break
            sorted_windows_read = True

        candidates = []
        for facet in first.get("facets", []):
            key = facet["key"]
            if key not in PARTITION_KEYS or key in used:
                continue
            values = [entry for entry in facet["values"] if 0 < entry["totalResults"] < expected]
            if not values:
                continue
            count_sum = sum(entry["totalResults"] for entry in values)
            score = (count_sum != expected, max(entry["totalResults"] for entry in values),
                     count_sum, PARTITION_KEYS.index(key))
            candidates.append((score, key, values))

        if expected > RESULT_WINDOW and len(found) < expected:
            for _, key, values in sorted(candidates, key=lambda candidate: candidate[0])[:1]:
                for entry in values:
                    child_filters = (*filters, (key, entry["value"]["id"]))
                    found.update(self._walk(child_filters, used | {key}))
                    if len(found) >= expected:
                        break
                if len(found) >= expected:
                    break
        if len(found) < expected:
            sorts = ("DATE:ASCENDING", "DATE:DESCENDING")
            if not sorted_windows_read:
                sorts = ("ASCENDING", "DESCENDING", *sorts)
            for sort in sorts:
                ordered, identifiers = self._window(filters, sort)
                if ordered["totalResults"] != expected:
                    self.issues.append(f"Count changed while sorting {filters}")
                found.update(identifiers)
                if len(found) >= expected:
                    break

        complete = len(found) == expected
        self.partitions.append({"filters": dict(filters), "expected": expected,
                                "collected": len(found), "complete": complete})
        log.info("%s: %d/%d variants; total unique=%d", dict(filters) or "all courses",
                 len(found), expected, len(self.items))
        return found

    def collect(self) -> tuple[list[dict], dict]:
        identifiers = self._walk((), frozenset())
        expected = self.partitions[-1]["expected"]
        report = {
            "source": ENDPOINT,
            "scope": {"type": "digital_course", "access": "free", "language": "en-US",
                      "difficulty": "all", "topics": "all", "roles": "all"},
            "expected_variants": expected,
            "collected_variants": len(identifiers),
            "missing_variants": max(0, expected - len(identifiers)),
            "complete": len(identifiers) == expected and not self.issues,
            "fetched_pages": self.fetched_pages,
            "cached_pages": self.cache_hits,
            "issues": self.issues,
            "partitions": self.partitions,
        }
        return [self.items[identifier] for identifier in sorted(identifiers)], report


def catalog_row(item: dict, collected_at: str) -> dict[str, str]:
    if item.get("languageCode") != "en-US" or (item.get("accessTier") or {}).get("value") != "free":
        raise ValueError(f"Unexpected catalog language or access tier: {item.get('id')}")
    if (item.get("type") or {}).get("value") != "digital_course":
        raise ValueError(f"Unexpected catalog item type: {item.get('id')}")
    level = (item.get("skillLevel") or {}).get("value", "")
    duration = item.get("duration") or {}
    minutes = duration.get("value")
    if not isinstance(minutes, (int, float)):
        minutes = ""
    if duration.get("unit") == "HOURS" and isinstance(minutes, (int, float)):
        minutes *= 60
    elif duration.get("unit") != "MINUTES":
        minutes = ""
    rating = item.get("reviewInsights") or {}
    average, count = rating.get("averageRating"), rating.get("reviewCount")
    rated = isinstance(average, (int, float)) and 0 < average <= 5 and type(count) is int and count > 0
    row = {
        "course_id": item["rootId"], "title": item["name"], "url": item["trainingObjectUrl"],
        "level": LEVELS.get(level, level), "retiring": (item.get("expirationDate") or {}).get("value") or "",
        "access": "무료", "product_type": "디지털 과정",
        "duration_text": f"{minutes} minutes" if minutes != "" else "", "duration_min": str(minutes),
        "language": "English", "other_languages": "", "description": item.get("description") or "",
        "objectives": " / ".join((item.get("objectives") or {}).get("values") or []),
        "aws_services": " / ".join((item.get("awsServices") or {}).get("valuesDisplayStrings") or []),
        "audience": " / ".join((item.get("intendedAudience") or {}).get("values") or []),
        "prerequisites": (item.get("prerequisites") or {}).get("value") or "",
        "source": "AWS Skill Builder public catalog", "collected_at": collected_at[:10],
        "rating_avg": f"{average:.1f}" if rated else "", "rating_count": str(count) if rated else "",
        "variant_id": item["id"], "domain": (item.get("domain") or {}).get("valueDisplayString") or "",
        "domain_id": (item.get("domain") or {}).get("value") or "",
        "roles": " / ".join((item.get("roles") or {}).get("valuesDisplayStrings") or []),
        "status": item.get("status") or "",
        "source_updated_at": (item.get("auditMetadata") or {}).get("lastUpdatedAt") or "",
        "catalog_collected_at": collected_at, "rating_collected_at": collected_at,
    }
    histogram = rating.get("ratingHistogram") or {}
    for stars, key in enumerate(STAR_KEYS, start=1):
        value = (histogram.get(key) or {}).get("count")
        row[f"rating_{stars}_count"] = str(value) if type(value) is int and value >= 0 else ""
    return row


def export_catalog(items: list[dict], output: Path, report: dict, baseline: Path) -> dict:
    rows = [catalog_row(item, item["_collected_at"]) for item in items]
    identifiers = {row["course_id"] for row in rows}
    if not rows or len(identifiers) != len(rows):
        raise ValueError("The catalog is empty or contains multiple English variants of the same course")
    old_ids = set()
    if baseline.is_file():
        with baseline.open(encoding="utf-8-sig", newline="") as stream:
            old_ids = {row["course_id"] for row in csv.DictReader(stream)}
    report.update({
        "exported_courses": len(rows), "new_courses": len(identifiers - old_ids),
        "baseline_courses": len(old_ids), "baseline_overlap": len(identifiers & old_ids),
        "baseline_only_ids": sorted(old_ids - identifiers),
        "levels": dict(Counter(row["level"] or "unspecified" for row in rows)),
        "domains": dict(Counter(row["domain"] or "unspecified" for row in rows)),
        "statuses": dict(Counter(row["status"] for row in rows)),
        "with_objectives": sum(bool(row["objectives"]) for row in rows),
        "with_ratings": sum(bool(row["rating_count"]) for row in rows),
        "recommendable": sum(bool(row["objectives"]) and not row["retiring"] and row["status"] == "ACTIVE"
                             for row in rows),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["course_id"]))
    temporary.replace(output)
    output.with_suffix(".summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=ROOT / "data/raw/course_catalog" / datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--output", type=Path, default=ROOT / "data/courses/catalog_all.csv")
    parser.add_argument("--request-gap", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    robots = RobotFileParser("https://skillbuilder.aws/robots.txt")
    robots.read()
    if not robots.can_fetch(USER_AGENT, ENDPOINT):
        raise SystemExit("robots.txt does not allow reading the catalog")
    collector = CatalogCollector(args.cache, fetch=PublicCatalogClient(args.request_gap))
    items, report = collector.collect()
    report = export_catalog(items, args.output, report, ROOT / "data/courses/courses.csv")
    print(json.dumps({key: value for key, value in report.items() if key != "partitions"},
                     ensure_ascii=False, indent=2))
    if not report["complete"]:
        raise SystemExit("Catalog coverage is incomplete; inspect the summary before using it as a complete catalog")


if __name__ == "__main__":
    main()
