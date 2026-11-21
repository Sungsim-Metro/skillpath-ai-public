import tempfile
import unittest
from pathlib import Path

from pipeline.courses.collect_catalog import CatalogCollector, catalog_row


def course(identifier, level="advanced", domain="Business applications"):
    return {
        "id": identifier,
        "rootId": f"root-{identifier}",
        "name": f"Course {identifier}",
        "languageCode": "en-US",
        "trainingObjectUrl": f"https://skillbuilder.aws/learn/root-{identifier}/course/{identifier}",
        "description": "A public course description.",
        "objectives": {"values": ["Understand a topic.", "Practice a task."]},
        "intendedAudience": {"values": ["Business users"]},
        "prerequisites": {"value": "None"},
        "duration": {"unit": "MINUTES", "value": 45},
        "skillLevel": {"value": level, "valueDisplayString": level.title()},
        "domain": {"valueDisplayString": domain},
        "roles": {"valuesDisplayStrings": ["Business User"]},
        "awsServices": {"valuesDisplayStrings": []},
        "type": {"value": "digital_course", "valueDisplayString": "Digital course"},
        "accessTier": {"value": "free", "valueDisplayString": "Free"},
        "status": "ACTIVE",
        "reviewInsights": {
            "averageRating": 4.5, "reviewCount": 2,
            "ratingHistogram": {"fourStar": {"count": 1}, "fiveStar": {"count": 1}},
        },
    }


def facet(key, counts):
    return {"key": key, "values": [
        {"value": {"id": value, "value": value}, "totalResults": count}
        for value, count in counts.items()
    ]}


class CatalogCollectionTest(unittest.TestCase):
    def test_partitions_the_result_window_and_fetches_the_second_page(self):
        calls = []

        def fetch(filters, page, sort=None):
            calls.append((filters, page))
            selected = dict(filters)
            if "courseLevel" not in selected:
                return {"totalResults": 120, "results": [{"item": course(str(index))} for index in range(50)],
                        "facets": [facet("courseLevel", {"fundamental": 60, "advanced": 60})]}
            start = 0 if selected["courseLevel"] == "fundamental" else 60
            offset = (page - 1) * 50
            return {"totalResults": 60, "facets": [], "results": [
                {"item": course(str(index))} for index in range(start + offset, min(start + offset + 50, start + 60))
            ]}

        with tempfile.TemporaryDirectory() as directory:
            collector = CatalogCollector(Path(directory), fetch=fetch)
            items, report = collector.collect()
        self.assertEqual(len(items), 120)
        self.assertTrue(report["complete"])
        self.assertEqual(report["expected_variants"], 120)
        self.assertEqual(sum(bool(filters) and page == 2 for filters, page in calls), 2)

    def test_overlapping_facets_are_deduplicated_and_missing_coverage_is_reported(self):
        def fetch(filters, page, sort=None):
            if not filters:
                return {"totalResults": 110, "results": [{"item": course(str(index))} for index in range(50)],
                        "facets": [facet("domain", {"one": 60, "two": 60})]}
            start = 0 if dict(filters)["domain"] == "one" else 40
            offset = (page - 1) * 50
            return {"totalResults": 60, "facets": [], "results": [
                {"item": course(str(index))} for index in range(start + offset, min(start + offset + 50, start + 60))
            ]}

        with tempfile.TemporaryDirectory() as directory:
            items, report = CatalogCollector(Path(directory), fetch=fetch).collect()
        self.assertEqual(len(items), 100)
        self.assertFalse(report["complete"])
        self.assertEqual(report["missing_variants"], 10)

    def test_cached_searches_resume_without_network_requests(self):
        def fetch(filters, page, sort=None):
            return {"totalResults": 1, "facets": [], "results": [{"item": course("one")}]}

        with tempfile.TemporaryDirectory() as directory:
            first = CatalogCollector(Path(directory), fetch=fetch).collect()

            def unexpected_fetch(filters, page, sort=None):
                self.fail("A cached page should not be fetched again")

            second = CatalogCollector(Path(directory), fetch=unexpected_fetch).collect()
        self.assertEqual(first[0], second[0])
        self.assertTrue(second[1]["complete"])

    def test_duplicate_page_results_cannot_claim_complete_coverage(self):
        def fetch(filters, page, sort=None):
            return {"totalResults": 60, "facets": [], "results": [
                {"item": course(str(index))} for index in range(50)
            ]}

        with tempfile.TemporaryDirectory() as directory:
            items, report = CatalogCollector(Path(directory), fetch=fetch).collect()
        self.assertEqual(len(items), 50)
        self.assertFalse(report["complete"])

    def test_opposite_name_orders_cover_courses_with_no_partition_metadata(self):
        def fetch(filters, page, sort=None):
            identifiers = list(range(150))
            if sort == "DESCENDING":
                identifiers.reverse()
            offset = (page - 1) * 50
            return {"totalResults": 150, "facets": [], "results": [
                {"item": course(str(identifier))} for identifier in identifiers[offset:offset + 50]
            ]}

        with tempfile.TemporaryDirectory() as directory:
            items, report = CatalogCollector(Path(directory), fetch=fetch).collect()
        self.assertEqual(len(items), 150)
        self.assertTrue(report["complete"])


class CatalogConversionTest(unittest.TestCase):
    def test_preserves_advanced_non_target_courses_and_ratings(self):
        row = catalog_row(course("one"), "2026-11-20T00:00:00+00:00")
        self.assertEqual(row["course_id"], "root-one")
        self.assertEqual(row["variant_id"], "one")
        self.assertEqual(row["level"], "고급")
        self.assertEqual(row["domain"], "Business applications")
        self.assertEqual(row["objectives"], "Understand a topic. / Practice a task.")
        self.assertEqual((row["rating_avg"], row["rating_count"]), ("4.5", "2"))
        self.assertEqual(row["rating_5_count"], "1")
        self.assertEqual(row["rating_collected_at"], "2026-11-20T00:00:00+00:00")

    def test_missing_metadata_stays_missing(self):
        item = course("one")
        item.update(objectives=None, prerequisites=None, reviewInsights=None, domain=None, skillLevel=None)
        row = catalog_row(item, "2026-11-20T00:00:00+00:00")
        for field in ("objectives", "prerequisites", "rating_avg", "rating_count", "domain", "level"):
            self.assertEqual(row[field], "")

    def test_other_languages_or_paid_courses_are_not_silently_mislabeled(self):
        for field, value in (("languageCode", "ko-KR"), ("accessTier", {"value": "subscription"})):
            with self.subTest(field=field):
                item = course("one")
                item[field] = value
                with self.assertRaises(ValueError):
                    catalog_row(item, "2026-11-20T00:00:00+00:00")

    def test_unknown_duration_is_not_exported_as_a_literal_none(self):
        item = course("one")
        item["duration"] = {"unit": "MINUTES", "value": None}
        row = catalog_row(item, "2026-11-20T00:00:00+00:00")
        self.assertEqual(row["duration_min"], "")
        self.assertEqual(row["duration_text"], "")


if __name__ == "__main__":
    unittest.main()
