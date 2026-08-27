import unittest

from indicator_engine import calculate_indicators
from seoul_closure_source_validation import (
    aggregate_dong_rows, api_to_quarter, compare_district_sources, parse_oa22172_zip,
    pooled_quarters, spearman, structural_budget_preview,
)


def row(q="20252", dong="11110515", industry="CS1", total=100, closed=2):
    return {"STDR_YYQU_CD": q, "ADSTRD_CD": dong, "ADSTRD_CD_NM": "동",
            "SIGNGU_CD": dong[:5], "SIGNGU_CD_NM": "종로구", "SVC_INDUTY_CD": industry,
            "SVC_INDUTY_CD_NM": industry, "SIMILR_INDUTY_STOR_CO": total,
            "CLSBIZ_STOR_CO": closed}


def complete_quarter(q, rate_offset=0):
    districts = {}
    from commercial_store_snapshot import SEOUL_DISTRICT_CODES
    for index, name in enumerate(SEOUL_DISTRICT_CODES):
        districts[name] = {"district": name, "total_store_count": 100,
                           "closed_store_count": index + 1 + rate_offset,
                           "closure_rate": index + 1 + rate_offset, "source_status": "ok"}
    return {"quarter": q, "district_count": 25, "status": "complete", "districts": districts}


class SeoulClosureSourceValidationTests(unittest.TestCase):
    def test_oa22172_actual_file_parsing_and_schema(self):
        rows, schema = parse_oa22172_zip("data/risk/seoul_closure/source/OA-22172_2025.zip")
        self.assertEqual(len(rows), 141218)
        self.assertIn("기준_년분기_코드", schema["actual_fields"])
        self.assertIn("폐업_점포_수", schema["actual_fields"])

    def test_quarter_parsing(self):
        self.assertEqual(api_to_quarter("20254"), "2025Q4")
        with self.assertRaises(ValueError): api_to_quarter("20255")

    def test_dong_to_district_aggregation(self):
        value = aggregate_dong_rows([row(industry="A"), row(industry="B", total=200, closed=6)], "2025Q2")
        self.assertEqual(value["districts"]["종로구"]["total_store_count"], 300)
        self.assertEqual(value["districts"]["종로구"]["closed_store_count"], 8)

    def test_duplicate_grain_detection(self):
        value = aggregate_dong_rows([row(), row()], "2025Q2")
        self.assertEqual(value["diagnostics"]["duplicate_grain_count"], 1)

    def test_oa22172_vs_oa22173_consistency(self):
        district = {"districts": {"종로구": {"total_store_count": 100, "closed_store_count": 2,
                                              "closure_rate": 2}}}
        dong = {"districts": dict(district["districts"])}
        self.assertEqual(compare_district_sources(dong, district)["exact_near_match_count"], 1)

    def test_four_quarter_pooled_numerator(self):
        value = pooled_quarters({q: complete_quarter(q) for q in ("2025Q2","2025Q3","2025Q4","2026Q1")})
        self.assertEqual(value["districts"]["강남구"]["pooled_closed_store_count"], 4)

    def test_four_quarter_pooled_denominator(self):
        value = pooled_quarters({q: complete_quarter(q) for q in ("2025Q2","2025Q3","2025Q4","2026Q1")})
        self.assertEqual(value["districts"]["강남구"]["pooled_total_store_count"], 400)

    def test_pooled_closure_rate(self):
        value = pooled_quarters({q: complete_quarter(q) for q in ("2025Q2","2025Q3","2025Q4","2026Q1")})
        self.assertEqual(value["districts"]["강남구"]["pooled_closure_rate"], 1)

    def test_pooled_percentile_direction(self):
        value = pooled_quarters({q: complete_quarter(q) for q in ("2025Q2","2025Q3","2025Q4","2026Q1")})
        self.assertLess(value["districts"]["강남구"]["pooled_percentile"],
                        value["districts"]["강동구"]["pooled_percentile"])

    def test_rank_stability(self):
        self.assertAlmostEqual(spearman({"a":1,"b":2,"c":3}, {"a":2,"b":4,"c":6}), 1)

    def test_missing_quarter_is_partial(self):
        value = pooled_quarters({"2026Q1": complete_quarter("2026Q1")})
        self.assertEqual(value["status"], "partial")

    def test_partial_quarter_is_excluded(self):
        partial = complete_quarter("2025Q4"); partial["status"] = "partial"
        value = pooled_quarters({"2025Q4": partial, "2026Q1": complete_quarter("2026Q1")})
        self.assertEqual(value["quarters"], ["2026Q1"])

    def test_source_provenance_artifact(self):
        import json
        from pathlib import Path
        value = json.loads(Path("data/risk/seoul_closure/quarterly/2025Q2.json").read_text(encoding="utf-8"))
        self.assertEqual(value["source_provenance"]["source_dataset"], "OA-22172")
        self.assertFalse(value["source_provenance"]["service_active"])

    def test_future_source_is_unavailable(self):
        import json
        from pathlib import Path
        value = json.loads(Path("data/risk/seoul_closure/manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(value["production_ready_source"])
        self.assertEqual(value["production_readiness"], "blocked")

    def test_budget_b_preserves_fixed_ten_and_production_risk_unchanged(self):
        pooled = pooled_quarters({q: complete_quarter(q) for q in ("2025Q2","2025Q3","2025Q4","2026Q1")})
        preview = structural_budget_preview(pooled, short_term=0)
        self.assertGreaterEqual(preview["distribution"]["candidate_b_fixed10_structural_max10"]["min"], 10)
        empty = {"count":0,"items":[]}
        data = {"weather":{},"special_day":empty,"festival":empty,"event":empty,
                "performance":empty,"sports":empty,"footfall":{}}
        self.assertEqual(calculate_indicators(data).operational_risk, 20)


if __name__ == "__main__": unittest.main()
