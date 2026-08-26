import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from quarterly_sales_baseline import (
    aggregate_quarter, build_calibration_preview, collect_quarterly_snapshot,
    load_quarterly_baseline, snapshot_paths, update_manifest,
)
from realtime_commerce_preview import (
    build_hybrid_preview, build_mapping_diagnostics, collect_reference,
    load_realtime_latest, save_realtime,
)


def sales_row(quarter="20261", district="강남구", code="A", amount="100", count="4"):
    return {"STDR_YYQU_CD": quarter, "SIGNGU_CD": "11680", "SIGNGU_CD_NM": district,
            "SVC_INDUTY_CD": code, "THSMON_SELNG_AMT": amount, "THSMON_SELNG_CO": count}


class QuarterlyBaselineTests(unittest.TestCase):
    def test_pagination_dedupe_aggregation_normalization_and_key_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock()
            client.estimated_sales_page.side_effect = [
                {"source_status": "ok", "total_count": 3,
                 "items": [sales_row(), sales_row(code="B", amount="300", count="6")]},
                {"source_status": "ok", "total_count": 3, "items": [sales_row()]},
            ]
            checkpoint = collect_quarterly_snapshot(client, "2026Q1", data_dir=tmp, page_size=2,
                                                     sleep=lambda _: None)
            self.assertEqual(checkpoint["status"], "complete")
            self.assertEqual(checkpoint["source_status"], "ok")
            self.assertEqual(checkpoint["raw_row_count"], 2)
            self.assertEqual(checkpoint["duplicate_grain_count"], 1)
            stores = Path(tmp) / "stores.json"
            stores.write_text(json.dumps({"districts": {"강남구": {"total_store_count": 20}}}), encoding="utf-8")
            aggregate = aggregate_quarter("2026Q1", data_dir=tmp, store_snapshot_path=stores,
                                          district_areas={"강남구": 2})
            row = aggregate["districts"]["강남구"]
            self.assertEqual(row["district_total_estimated_sales"], 400)
            self.assertEqual(row["district_total_estimated_transactions"], 10)
            self.assertEqual(row["industry_count"], 2)
            self.assertEqual(row["estimated_sales_per_store"], 20)
            self.assertEqual(row["estimated_sales_per_km2"], 200)
            preview = build_calibration_preview("2026Q1", data_dir=tmp)
            self.assertEqual(preview["districts"]["강남구"]["total_sales_percentile"], 50)
            self.assertFalse(preview["production_scoring_connected"])
            self.assertNotIn("secret-key", Path(snapshot_paths(tmp, "2026Q1")["raw"]).read_text())

    def test_quarter_snapshot_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = snapshot_paths(tmp, "2026Q1")["normalized"]
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"snapshot_quarter": "2026Q1"}), encoding="utf-8")
            loaded = load_quarterly_baseline("2026Q2", data_dir=tmp)
            self.assertEqual((loaded["source_status"], loaded["age_quarters"]), ("fallback", 1))


class RealtimePreviewTests(unittest.TestCase):
    def fixtures(self, tmp):
        places = {"places": [{"area_cd": "P1", "area_nm": "같은곳", "category": "발달상권"},
                              {"area_cd": "P2", "area_nm": "미매핑"}]}
        mappings = {"mappings": [{"area_cd": "P1", "area_nm": "같은곳", "cross_boundary": False,
                                    "effective_candidates": [{"district": "강남구", "intersection_ratio": 1.0}]}]}
        pp, mp = Path(tmp) / "places.json", Path(tmp) / "maps.json"
        pp.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
        mp.write_text(json.dumps(mappings, ensure_ascii=False), encoding="utf-8")
        return pp, mp

    def test_reference_mapping_reuse_and_unmapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            pp, mp = self.fixtures(tmp)
            client = Mock()
            client.realtime_commerce.side_effect = [
                {"source_status": "ok", "area_cd": "P1", "area_nm": "같은곳", "commercial": {}},
                {"source_status": "ok", "area_cd": "P2", "area_nm": "미매핑", "commercial": {}},
            ]
            ref = collect_reference(client, reference_date="2026-08-23", oa_places_path=pp, data_dir=tmp)
            diag = build_mapping_diagnostics(ref, oa_places_path=pp, oa_mappings_path=mp, data_dir=tmp)
            self.assertEqual(ref["place_count"], 2)
            self.assertEqual(diag["oa21285_code_reuse_count"], 1)
            self.assertEqual(diag["unmapped_count"], 1)

    def test_latest_history_are_separate_and_key_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {"source_status": "ok", "area_cd": "P1", "area_nm": "place",
                      "commercial": {"AREA_CMRCL_LVL": "보통", "CMRCL_TIME": "2026-08-23 15:00"}}
            paths = save_realtime(result, {"assigned_district": "강남구"}, data_dir=tmp,
                                  received_at=datetime(2026, 8, 23, 15, 1, tzinfo=ZoneInfo("Asia/Seoul")))
            self.assertNotEqual(paths["latest"], paths["history"])
            for path in (paths["latest"], paths["history"]):
                self.assertTrue(path.is_file())
                self.assertNotIn("secret-key", path.read_text())
            loaded = load_realtime_latest("P1", data_dir=tmp,
                                          now=datetime(2026, 8, 23, 15, 11, tzinfo=ZoneInfo("Asia/Seoul")))
            self.assertEqual(loaded["age_minutes"], 10)
            self.assertEqual(loaded["latest_source_time"], "2026-08-23 15:00")

    def test_hybrid_is_preview_only(self):
        value = build_hybrid_preview("강남구", {"district_total_estimated_sales": 1},
                                     {"source_status": "no_data"})
        self.assertEqual(value["fallback_mode"], "baseline_only")
        self.assertEqual(value["hybrid_status"], "preview_only")


class ProductionIsolationTests(unittest.TestCase):
    def test_production_spending_module_has_no_preview_import(self):
        source = (Path(__file__).resolve().parents[1] / "indicator_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("quarterly_sales_baseline", source)
        self.assertNotIn("realtime_commerce_preview", source)


if __name__ == "__main__":
    unittest.main()
