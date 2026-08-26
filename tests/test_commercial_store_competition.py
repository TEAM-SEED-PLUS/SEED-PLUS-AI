import unittest
from unittest.mock import patch

from commercial_store_snapshot import collect_count_snapshot
from indicator_engine import (
    calculate_competition_contribution,
    calculate_indicators,
    compute_commercial_store_bonus,
    compute_sdot_realtime_bonus,
)
from llm_feed_writer import build_llm_context
from v1_config import COMMERCIAL_STORE_COMPETITION_ENABLED


def loaded(percentile=50, status="complete"):
    return {
        "data": {"source_status": "complete", "districts": {"강남구": {
            "store_count": 100, "district_area_km2": 2,
            "stores_per_km2": 50, "density_percentile": percentile, "source_status": "ok",
        }}},
        "requested_quarter": "2026Q3", "source_quarter": "2026Q3",
        "source_status": status, "age_quarters": 0,
    }


def normalized(spike=2, status="ok"):
    empty = {"count": 0, "items": []}
    return {
        "query": {"district": "강남구", "date": "2026-08-23", "time_band": "오후"},
        "weather": {}, "special_day": dict(empty), "festival": dict(empty),
        "event": dict(empty), "performance": dict(empty), "sports": dict(empty),
        "overview": {"content_tag_counts": {}},
        "footfall": {"summary": {"visitor_spike_ratio": spike}, "source_status": {"status": status}},
        "source_status": {"footfall": {"status": status}},
    }


class CountClient:
    def stores_in_dong(self, div_id, key, *, page_no=1, rows=1, **kwargs):
        return {"total_count": int(key), "items": [], "status": "ok"}


class CommercialCompetitionTests(unittest.TestCase):
    def test_feature_flag_enabled(self):
        self.assertTrue(COMMERCIAL_STORE_COMPETITION_ENABLED)

    def test_store_bonus_boundaries_and_cap(self):
        self.assertEqual(compute_commercial_store_bonus(0), 0)
        self.assertEqual(compute_commercial_store_bonus(50), 6)
        self.assertEqual(compute_commercial_store_bonus(100), 12)
        self.assertEqual(compute_commercial_store_bonus(999), 12)

    def test_sdot_realtime_shape_and_cap(self):
        self.assertEqual(compute_sdot_realtime_bonus(0), 0)
        self.assertEqual(compute_sdot_realtime_bonus(8), 2)
        self.assertEqual(compute_sdot_realtime_bonus(16), 4)
        self.assertEqual(compute_sdot_realtime_bonus(999), 4)

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value=loaded(50))
    def test_commercial_plus_realtime(self, _):
        value = calculate_competition_contribution(normalized(), {
            "competition_bonus": 8, "spike": 2, "competition_source_status": {"status": "ok"},
        })
        self.assertEqual(value["competition_mode"], "commercial_plus_realtime")
        self.assertEqual(value["commercial_store_bonus"], 6)
        self.assertEqual(value["sdot_realtime_bonus"], 2)
        self.assertEqual(value["applied_footfall_competition_bonus"], 8)

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value=loaded(50))
    def test_commercial_only_does_not_renormalize(self, _):
        value = calculate_competition_contribution(normalized(None, "failed"), {
            "competition_bonus": 0, "spike": None, "competition_source_status": {"status": "failed"},
        })
        self.assertEqual(value["competition_mode"], "commercial_only")
        self.assertEqual(value["applied_footfall_competition_bonus"], 6)
        self.assertTrue(value["data_insufficient"])

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value={
        "data": None, "requested_quarter": "2026Q3", "source_quarter": None,
        "source_status": "failed", "age_quarters": None,
    })
    def test_commercial_failure_uses_legacy_sdot_max16(self, _):
        value = calculate_competition_contribution(normalized(), {
            "competition_bonus": 16, "spike": 3, "competition_source_status": {"status": "ok"},
        })
        self.assertEqual(value["competition_mode"], "legacy_sdot_fallback")
        self.assertEqual(value["applied_footfall_competition_bonus"], 16)
        self.assertEqual(value["source_status"], "fallback")

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value={
        "data": None, "requested_quarter": "2026Q3", "source_quarter": None,
        "source_status": "failed", "age_quarters": None,
    })
    def test_both_failure_is_base_only(self, _):
        value = calculate_competition_contribution(normalized(None, "failed"), {
            "competition_bonus": 0, "spike": None, "competition_source_status": {"status": "failed"},
        })
        self.assertEqual(value["competition_mode"], "base_only")
        self.assertEqual(value["applied_footfall_competition_bonus"], 0)

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value=loaded(100))
    def test_final_score_base_content_preserved_and_risk_unchanged(self, _):
        result = calculate_indicators(normalized(spike=3))
        self.assertEqual(result.competition_pressure, 34)  # base 18 + store 12 + realtime 4
        self.assertEqual(result.operational_risk, 20)
        self.assertEqual(result.contribution_map["competition"]["final"], 34)
        self.assertFalse(result.contribution_map["competition"]["industry_competition_applied"])
        self.assertFalse(result.contribution_map["competition"]["closure_filter_applied"])

    @patch("commercial_store_snapshot.load_scoring_snapshot", return_value=loaded(50))
    def test_llm_semantics_are_density_not_industry_or_closure(self, _):
        data = normalized()
        indicators = calculate_indicators(data)
        context = build_llm_context(data["query"], data, indicators)
        semantics = context["competition"]["data_semantics"]
        self.assertIn("전체 점포", semantics)
        self.assertIn("동일 업종 경쟁률", semantics)
        self.assertFalse(context["competition"]["industry_competition_applied"])
        self.assertFalse(context["competition"]["closure_filter_applied"])

    def test_count_snapshot_area_no_data_and_percentile_exclusion(self):
        with patch("commercial_store_snapshot.SEOUL_DISTRICT_CODES", {"강남구": "100", "종로구": "200"}):
            with self.subTest("missing area"):
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    result = collect_count_snapshot(
                        "2026Q3", data_dir=tmp, client=CountClient(),
                        district_areas={"강남구": 10, "종로구": 0}, sleep=lambda _: None,
                    )
                    self.assertEqual(result["api_call_count"], 2)
                    self.assertEqual(result["districts"]["강남구"]["stores_per_km2"], 10)
                    self.assertIsNone(result["districts"]["종로구"]["stores_per_km2"])
                    self.assertIsNone(result["districts"]["종로구"]["density_percentile"])


if __name__ == "__main__":
    unittest.main()
