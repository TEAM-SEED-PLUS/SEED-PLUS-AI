import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from consumption_hybrid import (
    collect_realtime_once, load_realtime_commerce, load_sales_baseline,
    query_quarter, realtime_commerce_bonus, sales_baseline_bonus,
)
from indicator_engine import calculate_consumption_hybrid_contribution, calculate_indicators
from quarterly_sales_baseline import snapshot_paths
from sales_store_alignment import store_paths
from v1_config import CONSUMPTION_HYBRID_ENABLED


def empty_data():
    empty = {"count": 0, "items": []}
    return {"query": {"district": "강남구", "date": "2026-08-23", "time_band": "점심"},
            "weather": {}, "special_day": empty, "festival": empty, "event": empty,
            "performance": empty, "sports": empty, "overview": {"content_tag_counts": {}},
            "footfall": {"summary": {}, "contributions": {"spending_level_bonus": 8},
                         "footfall_sources": {"inflow": "oa21285", "spending": "oa21285", "competition": "sdot"}},
            "source_status": {}}


class BonusTests(unittest.TestCase):
    def test_sales_boundaries_and_cap(self):
        self.assertEqual([sales_baseline_bonus(x) for x in (0, 50, 100, 200)], [0, 3.5, 7, 7])

    def test_realtime_boundaries_and_cap(self):
        self.assertEqual([realtime_commerce_bonus(x) for x in (0, 50, 100, 200)], [0, 1.5, 3, 3])

    def test_hybrid_modes_caps_and_no_double_count(self):
        footfall = {"spending_bonus": 8}
        with patch("indicator_engine.CONSUMPTION_HYBRID_ENABLED", True):
            normal = calculate_consumption_hybrid_contribution({"consumption_hybrid": {
                "baseline": {"source_status": "ok", "bonus": 7},
                "realtime": {"source_status": "ok", "bonus": 3}}}, footfall)
            baseline = calculate_consumption_hybrid_contribution({"consumption_hybrid": {
                "baseline": {"source_status": "ok", "bonus": 7},
                "realtime": {"source_status": "failed", "bonus": 3}}}, footfall)
            fallback = calculate_consumption_hybrid_contribution({"consumption_hybrid": {
                "baseline": {"source_status": "failed"},
                "realtime": {"source_status": "ok", "bonus": 3}}}, footfall)
        self.assertEqual((normal["mode"], normal["applied_bonus"]), ("sales_baseline_plus_realtime", 10))
        self.assertEqual(normal["legacy_oa21285_fallback"]["applied_bonus"], 0)
        self.assertFalse(normal["oa21285_spending_double_counted"])
        self.assertEqual((baseline["mode"], baseline["applied_bonus"]), ("sales_baseline_only", 7))
        self.assertEqual((fallback["mode"], fallback["applied_bonus"]), ("legacy_oa_spending_fallback", 8))


class SnapshotTests(unittest.TestCase):
    def write_baseline(self, root, quarter="2026Q1"):
        path = store_paths(root, quarter)["normalized"]; path.parent.mkdir(parents=True, exist_ok=True)
        rows = {f"구{i}": {} for i in range(24)}
        rows["강남구"] = {"sales_per_matched_store": 10, "candidate_a_sales_per_matched_store_percentile": 50}
        path.write_text(json.dumps({"districts": rows}), encoding="utf-8")
        checkpoint = store_paths(root, quarter)["checkpoint"]; checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"source_status": "ok"}), encoding="utf-8")

    def write_realtime(self, root, district="강남구", source_time="20260823 1540"):
        latest = Path(root) / "realtime" / "latest"; latest.mkdir(parents=True, exist_ok=True)
        for code, amount in (("P1", 100), ("P2", 300)):
            (latest / f"{code}.json").write_text(json.dumps({"area_cd": code, "source_status": "ok",
                "source_timestamp": source_time, "received_at": "2026-08-23T15:41:00+09:00",
                "payment_amount_midpoint": amount,
                "district_mapping": {"assigned_district": district}}), encoding="utf-8")

    def test_quarter_and_complete_snapshot_fallback(self):
        self.assertEqual(query_quarter("2026-08-23"), "2026Q3")
        with tempfile.TemporaryDirectory() as tmp:
            self.write_baseline(tmp)
            value = load_sales_baseline("강남구", "2026Q3", data_dir=tmp)
            self.assertEqual((value["source_quarter"], value["age_quarters"], value["source_status"]),
                             ("2026Q1", 2, "fallback"))
            self.assertEqual(value["bonus"], 3.5)

    def test_payment_percentile_district_mean_and_date_band_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_realtime(tmp)
            ok = load_realtime_commerce("강남구", "2026-08-23", "점심", data_dir=tmp)
            wrong_date = load_realtime_commerce("강남구", "2026-08-24", "점심", data_dir=tmp)
            wrong_band = load_realtime_commerce("강남구", "2026-08-23", "오후", data_dir=tmp)
            jungnang = load_realtime_commerce("중랑구", "2026-08-23", "점심", data_dir=tmp)
            self.assertEqual(ok["payment_percentile"], 50)
            self.assertEqual(ok["bonus"], 1.5)
            self.assertEqual((wrong_date["source_status"], wrong_band["source_status"]), ("no_data", "no_data"))
            self.assertEqual((jungnang["source_status"], jungnang["payment_percentile"]), ("no_data", None))

    def test_probe_collector_skips_same_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ref = root / "ref.json"; mapping = root / "map.json"
            ref.write_text(json.dumps({"places": [{"area_cd": "P1"}]}), encoding="utf-8"); mapping.write_text(json.dumps({"mappings": []}), encoding="utf-8")
            manifest = root / "realtime" / "manifest.json"; manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"latest_commerce_time": "20260823 1540"}), encoding="utf-8")
            client = Mock(); client.realtime_commerce.return_value = {"source_status": "ok", "commercial": {"CMRCL_TIME": "20260823 1540"}}
            value = collect_realtime_once(client=client, data_dir=root, reference_path=ref, mapping_path=mapping)
            self.assertEqual((value["status"], value["api_call_count"]), ("unchanged", 1))


class RegressionTests(unittest.TestCase):
    def test_feature_flag_activation_path_completed(self):
        self.assertTrue(CONSUMPTION_HYBRID_ENABLED)

    def test_tourapi_oa_inflow_competition_risk_and_semantics(self):
        data = empty_data(); data["consumption_hybrid"] = {
            "baseline": {"source_status": "ok", "bonus": 7},
            "realtime": {"source_status": "ok", "bonus": 3}}
        with patch("indicator_engine.CONSUMPTION_HYBRID_ENABLED", True):
            result = calculate_indicators(data)
        self.assertEqual(result.contribution_map["consumption_hybrid"]["applied_bonus"], 10)
        self.assertEqual(result.contribution_map["footfall"]["legacy_spending_bonus"], 8)
        self.assertEqual(result.contribution_map["footfall"]["applied_spending_bonus"], 0)
        self.assertIn("추정매출", " ".join(result.evidence["reason_summary"]))
        self.assertEqual(result.operational_risk, 20)


if __name__ == "__main__": unittest.main()
