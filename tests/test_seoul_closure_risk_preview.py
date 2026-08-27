import json
import tempfile
import unittest
from pathlib import Path

from indicator_engine import calculate_indicators
from seoul_closure_risk_preview import (
    KOSIS_SEMANTICS, SOURCE_SEMANTICS, aggregate_districts, build_preview,
    duplicate_diagnostics, load_preview, normalize_districts, read_snapshot,
    recent_quarter_candidates, row_consistency,
)


def row(district="강남구", code="CS1", total=100, closed=3, rate=3.0, quarter="20261"):
    return {"STDR_YYQU_CD": quarter, "SIGNGU_CD": "11680", "SIGNGU_CD_NM": district,
            "SVC_INDUTY_CD": code, "SVC_INDUTY_CD_NM": code,
            "SIMILR_INDUTY_STOR_CO": total, "STOR_CO": total - 10, "FRC_STOR_CO": 10,
            "CLSBIZ_STOR_CO": closed, "CLSBIZ_RT": rate}


class SeoulClosureRiskPreviewTests(unittest.TestCase):
    def test_existing_snapshot_is_reused(self):
        rows = read_snapshot("2026Q1")
        self.assertEqual(len(rows), 2495)

    def test_closure_and_total_store_parsing_and_district_aggregation(self):
        result = aggregate_districts([row(code="A", total=100, closed=3), row(code="B", total=50, closed=2)])
        self.assertEqual(result["강남구"]["closed_store_count"], 5)
        self.assertEqual(result["강남구"]["total_store_count"], 150)
        self.assertAlmostEqual(result["강남구"]["closure_rate"], 100 / 30)

    def test_row_closure_rate_consistency(self):
        result = row_consistency([row(total=100, closed=3, rate=3.0), row(total=200, closed=5, rate=2.5)])
        self.assertEqual(result["near_match_rate"], 1.0)
        self.assertEqual(result["mismatch_row_count"], 0)

    def test_zero_denominator_is_no_data(self):
        result = aggregate_districts([row(total=0, closed=0, rate=0)])
        self.assertIsNone(result["강남구"]["closure_rate"])
        self.assertEqual(result["강남구"]["source_status"], "no_data")

    def test_service_industry_duplicate_diagnostics(self):
        result = duplicate_diagnostics([row(), row()])
        self.assertEqual(result["duplicate_grain_count"], 1)

    def test_percentile_reuses_average_rank_utility(self):
        rows = [row("강남구", total=100, closed=1), row("마포구", total=100, closed=3),
                row("종로구", total=100, closed=3)]
        for item, code in zip(rows, ("11680", "11440", "11110")):
            item["SIGNGU_CD"] = code
        districts = aggregate_districts(rows)
        normalized, _ = normalize_districts(districts)
        self.assertEqual(normalized["강남구"]["closure_rate_percentile"], 0.0)
        self.assertEqual(normalized["마포구"]["closure_rate_percentile"], 75.0)

    def test_recent_quarter_mean_and_pooled(self):
        quarters = {
            "2025Q4": {"강남구": {"closure_rate": 2.0, "closed_store_count": 2, "total_store_count": 100}},
            "2026Q1": {"강남구": {"closure_rate": 4.0, "closed_store_count": 8, "total_store_count": 200}},
        }
        result = recent_quarter_candidates(quarters)
        district = result["districts"]["강남구"]
        self.assertEqual(district["four_quarter_mean_rate"], 3.0)
        self.assertAlmostEqual(district["four_quarter_pooled_rate"], 10 / 3)
        self.assertEqual(result["stability_status"], "insufficient_quarters")

    def test_quarter_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "normalized" / "2026Q1.json"
            path.parent.mkdir(parents=True); path.write_text("{}", encoding="utf-8")
            result = load_preview("2026Q3", risk_dir=temporary)
            self.assertEqual((result["source_quarter"], result["age_quarters"], result["source_status"]),
                             ("2026Q1", 2, "fallback"))

    def test_kosis_and_oa_semantics_are_separate(self):
        self.assertIn("점포 폐업률", SOURCE_SEMANTICS)
        self.assertIn("기업 소멸률", KOSIS_SEMANTICS)
        self.assertNotEqual(SOURCE_SEMANTICS, KOSIS_SEMANTICS)

    def test_preview_is_not_connected_to_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumption = Path(temporary) / "consumption"
            raw = consumption / "baseline" / "oa22173_raw" / "2026Q1.jsonl"
            raw.parent.mkdir(parents=True)
            raw.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            preview = build_preview("2026Q1", consumption_dir=consumption,
                                    risk_dir=Path(temporary) / "risk")
            self.assertTrue(preview["preview_only"])
            self.assertFalse(preview["production_scoring_connected"])

    def test_production_risk_unchanged(self):
        empty = {"count": 0, "items": []}
        data = {"weather": {}, "special_day": empty, "festival": empty, "event": empty,
                "performance": empty, "sports": empty, "footfall": {}}
        self.assertEqual(calculate_indicators(data).operational_risk, 20)


if __name__ == "__main__":
    unittest.main()
