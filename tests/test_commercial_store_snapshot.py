import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commercial_store_api import CommercialStoreAPIError
from commercial_store_category_mapping import WEATHERFEED_CATEGORY_MAPPING
from commercial_store_snapshot import (
    build_snapshot_artifacts,
    collect_district,
    collect_seoul_snapshot,
    get_district_industry_count,
    load_snapshot,
    percentile_ranks,
    snapshot_paths,
)
from indicator_engine import calculate_indicators


def store(store_id, district="강남구", code="I21201", lat=37.5, lon=127.0):
    return {
        "bizesId": store_id, "bizesNm": f"store-{store_id}",
        "indsLclsCd": "I2", "indsLclsNm": "음식",
        "indsMclsCd": "I212", "indsMclsNm": "비알코올",
        "indsSclsCd": code, "indsSclsNm": "카페",
        "ctprvnCd": "11", "ctprvnNm": "서울특별시",
        "signguCd": "11680", "signguNm": district,
        "adongCd": "1", "adongNm": "역삼동", "ldongCd": "2", "ldongNm": "역삼동",
        "rdnmAdr": "도로명", "lnoAdr": "지번", "lat": lat, "lon": lon,
        "businessNumber": "must-not-be-saved",
    }


class FakeClient:
    def __init__(self, pages=None, failures=None):
        self.pages = pages or {}
        self.failures = failures or set()
        self.calls = []

    def stores_in_dong(self, div_id, key, *, page_no=1, rows=1000, **kwargs):
        self.calls.append((key, page_no))
        if page_no in self.failures:
            raise CommercialStoreAPIError("failed without secret")
        items = self.pages.get(page_no, [])
        total = max(1, max(self.pages, default=1)) * 1000
        if self.pages:
            total = 1001 if max(self.pages) == 2 else len(items)
        return {"status": "ok", "total_count": total, "items": items, "page_no": page_no}


class CommercialStoreSnapshotTests(unittest.TestCase):
    def test_pagination_dedupe_district_save_manifest_and_key_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient({1: [store("A"), store("B")], 2: [store("B"), store("C")]})
            result = collect_district(client, "2026Q3", "강남구", data_dir=tmp, sleep=lambda _: None)
            self.assertEqual(result["expected_pages"], 2)
            self.assertEqual(result["collected_pages"], [1, 2])
            self.assertEqual(result["deduplicated_count"], 3)
            self.assertEqual(result["duplicate_count"], 1)
            raw = (snapshot_paths(tmp, "2026Q3")["raw"] / "강남구.jsonl").read_text()
            self.assertNotIn("businessNumber", raw)
            self.assertNotIn("secret", raw)

    def test_resume_collects_only_missing_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = FakeClient({1: [store("A")], 2: [store("B")]}, failures={2})
            result = collect_district(first, "2026Q3", "강남구", data_dir=tmp, retries=0, sleep=lambda _: None)
            self.assertEqual(result["status"], "partial")
            second = FakeClient({2: [store("B")]})
            result = collect_district(second, "2026Q3", "강남구", data_dir=tmp, retries=0, sleep=lambda _: None)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(second.calls, [("11680", 2)])

    def test_partial_district_does_not_raise_for_whole_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "commercial_store_snapshot.SEOUL_DISTRICT_CODES", {"강남구": "11680"}
        ):
            client = FakeClient({1: [store("A")], 2: [store("B")]}, failures={2})
            manifest = collect_seoul_snapshot("2026Q3", data_dir=tmp, client=client)
            self.assertEqual(manifest["source_status"], "partial")
            self.assertEqual(manifest["partial_district_count"], 1)

    def test_aggregate_density_percentile_taxonomy_and_industry_lookup(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "commercial_store_snapshot.SEOUL_DISTRICT_CODES", {"강남구": "11680", "종로구": "11110"}
        ):
            paths = snapshot_paths(tmp, "2026Q3")
            paths["raw"].mkdir(parents=True)
            for district, rows in {"강남구": [store("A"), store("B")], "종로구": [store("C", "종로구")]}.items():
                with (paths["raw"] / f"{district}.jsonl").open("w") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                paths["checkpoints"].mkdir(parents=True, exist_ok=True)
                (paths["checkpoints"] / f"{district}.json").write_text(json.dumps({"status": "complete"}))
            artifacts = build_snapshot_artifacts(
                "2026Q3", data_dir=tmp, district_areas={"강남구": 2.0, "종로구": 2.0}
            )
            gangnam = artifacts["aggregate"]["districts"]["강남구"]
            self.assertEqual(gangnam["stores_per_km2"], 1.0)
            self.assertEqual(gangnam["total_store_count_percentile"], 100.0)
            self.assertEqual(artifacts["taxonomy"]["diagnostics"], {
                "large_count": 1, "middle_count": 1, "small_count": 1,
            })
            lookup = get_district_industry_count(artifacts["aggregate"], "강남구", "small", "I21201")
            self.assertEqual(lookup["industry_store_count"], 2)
            self.assertEqual(lookup["same_industry_ratio"], 1.0)

    def test_percentile_average_rank(self):
        self.assertEqual(percentile_ranks({"a": 1, "b": 2, "c": 2, "d": 4}), {
            "a": 0.0, "b": 50.0, "c": 50.0, "d": 100.0,
        })

    def test_pending_mapping_has_no_guessed_codes(self):
        self.assertIn("카페", WEATHERFEED_CATEGORY_MAPPING)
        for mapping in WEATHERFEED_CATEGORY_MAPPING.values():
            self.assertEqual(mapping["status"], "pending_review")
            self.assertEqual(mapping["commercial_api_codes"], [])
            self.assertIsNone(mapping["commercial_api_level"])

    def test_quarterly_fallback_has_unbounded_age_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = snapshot_paths(tmp, "2025Q1")
            old["aggregated"].parent.mkdir(parents=True)
            old["aggregated"].write_text(json.dumps({"snapshot_quarter": "2025Q1", "districts": {}}))
            old["manifest"].write_text(json.dumps({"snapshots": {"2025Q1": {"source_status": "complete"}}}))
            loaded = load_snapshot("2026Q3", data_dir=tmp)
            self.assertEqual(loaded["source_status"], "fallback")
            self.assertEqual(loaded["source_quarter"], "2025Q1")
            self.assertEqual(loaded["age_quarters"], 6)

    def test_existing_competition_score_is_unchanged_by_new_modules(self):
        normalized = {
            "overview": {"content_tag_counts": {}},
            "festival": {"count": 0, "items": []}, "event": {"count": 0, "items": []},
            "performance": {"count": 0, "items": []}, "sports": {"count": 0, "items": []},
            "weather": {}, "special_day": {}, "tourism_baseline": {},
            "footfall": {"summary": {"visitor_spike_ratio": 2.0}, "source_status": {}},
            "source_status": {},
        }
        self.assertEqual(calculate_indicators(normalized).competition_pressure, 27)


if __name__ == "__main__":
    unittest.main()
