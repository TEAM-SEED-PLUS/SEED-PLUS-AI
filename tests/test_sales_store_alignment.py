import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from quarterly_sales_baseline import snapshot_paths
from realtime_commerce_preview import (
    build_payment_aggregation_preview, same_band_history_preview, save_realtime,
)
from sales_store_alignment import build_aligned_preview, collect_oa22173, exact_join, store_paths


def row(kind, quarter="20261", district_code="11680", industry="A", value=100):
    base = {"STDR_YYQU_CD": quarter, "SIGNGU_CD": district_code, "SIGNGU_CD_NM": "강남구",
            "SVC_INDUTY_CD": industry, "SVC_INDUTY_CD_NM": industry}
    if kind == "sales": base.update({"THSMON_SELNG_AMT": value, "THSMON_SELNG_CO": 2})
    else: base.update({"SIMILR_INDUTY_STOR_CO": value, "STOR_CO": value, "FRC_STOR_CO": 0})
    return base


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


class AlignmentTests(unittest.TestCase):
    def test_oa22173_pagination_and_quarter_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock(); client.district_stores_page.side_effect = [
                {"total_count": 2, "items": [row("store"), row("store", quarter="20254")]},
                {"total_count": 2, "items": [row("store", industry="B")]},
            ]
            result = collect_oa22173(client, "2026Q1", data_dir=tmp, page_size=1, sleep=lambda _: None)
            self.assertEqual(result["source_status"], "ok")
            self.assertEqual(result["raw_row_count"], 2)

    def test_exact_code_join_unmatched_zero_store_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(snapshot_paths(tmp, "2026Q1")["raw"],
                        [row("sales", value=100), row("sales", industry="B", value=300),
                         row("sales", industry="C", value=50)])
            write_jsonl(store_paths(tmp, "2026Q1")["raw"],
                        [row("store", value=10), row("store", industry="B", value=0),
                         row("store", industry="D", value=4)])
            external = Path(tmp) / "external.json"; external.write_text(json.dumps({"districts": {}}), encoding="utf-8")
            result = exact_join("2026Q1", data_dir=tmp, external_snapshot_path=external)
            diag = result["diagnostics"]
            self.assertEqual((diag["matched_row_count"], diag["sales_only_count"], diag["store_only_count"]), (2, 1, 1))
            joined = {x["grain"]["service_industry_code"]: x for x in result["joined_rows"]}
            self.assertEqual(joined["A"]["sales_per_store_industry"], 10)
            self.assertIsNone(joined["B"]["sales_per_store_industry"])
            district = result["districts"]["강남구"]
            self.assertEqual(district["sales_per_matched_store"], 40)
            self.assertEqual(district["denominator_source"], "seoul_commercial_analysis_OA22173")
            preview = build_aligned_preview("2026Q1", data_dir=tmp)
            self.assertEqual(preview["districts"]["강남구"]["candidate_a_sales_per_matched_store_percentile"], 50)


class PaymentPreviewTests(unittest.TestCase):
    def result(self, source_time="20260823 1500"):
        return {"area_cd": "P1", "area_nm": "place", "source_status": "ok", "commercial": {
            "AREA_CMRCL_LVL": "바쁜", "AREA_SH_PAYMENT_CNT": "4",
            "AREA_SH_PAYMENT_AMT_MIN": "100", "AREA_SH_PAYMENT_AMT_MAX": "300",
            "CMRCL_TIME": source_time, "CMRCL_RSB": [{"RSB_LRG_CTGR": "음식",
                "RSB_MID_CTGR": "한식", "RSB_PAYMENT_LVL": "보통", "RSB_SH_PAYMENT_CNT": 2,
                "RSB_SH_PAYMENT_AMT_MIN": 50, "RSB_SH_PAYMENT_AMT_MAX": 70, "RSB_MCT_CNT": 3}]}}

    def test_payment_fields_midpoint_time_and_duplicate_prevention(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 8, 23, 15, tzinfo=ZoneInfo("Asia/Seoul"))
            first = save_realtime(self.result(), {"assigned_district": "강남구"}, data_dir=tmp, received_at=now)
            second = save_realtime(self.result(), {"assigned_district": "강남구"}, data_dir=tmp, received_at=now)
            payload = json.loads(first["latest"].read_text(encoding="utf-8"))
            self.assertEqual((payload["payment_count"], payload["payment_amount_midpoint"]), (4, 200))
            self.assertEqual(payload["source_timestamp"], "20260823 1500")
            self.assertEqual(payload["industry_commerce"][0]["RSB_MCT_CNT"], 3)
            self.assertTrue(first["history_created"]); self.assertFalse(second["history_created"])

    def test_mapping_aggregation_cross_boundary_and_jungnang_no_data(self):
        mapping = {"mappings": [{"area_cd": "P1", "assigned_district": "강남구", "cross_boundary": True}]}
        snapshots = [{"area_cd": "P1", "payment_amount_midpoint": 200, "payment_count": 4}]
        result = build_payment_aggregation_preview(snapshots, mapping, {"P1": 10})
        self.assertEqual(result["districts"]["강남구"]["candidate_b_population_weighted_mean"], 200)
        self.assertEqual(result["districts"]["중랑구"]["source_status"], "no_data")
        self.assertIsNone(result["districts"]["중랑구"]["candidate_a_amount_simple_mean"])

    def test_same_band_history_ratio(self):
        current = {"area_cd": "P1", "source_timestamp": "now", "payment_amount_midpoint": 200,
                   "payment_count": 10}
        history = [{"area_cd": "P1", "source_timestamp": "old", "payment_amount_midpoint": 100,
                    "payment_count": 5}]
        value = same_band_history_preview(current, history)
        self.assertEqual((value["payment_amount_ratio"], value["payment_count_ratio"]), (2, 2))


class ProductionIsolationTests(unittest.TestCase):
    def test_production_spending_unchanged(self):
        source = (Path(__file__).resolve().parents[1] / "indicator_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("sales_store_alignment", source)
        self.assertNotIn("build_payment_aggregation_preview", source)


if __name__ == "__main__": unittest.main()
