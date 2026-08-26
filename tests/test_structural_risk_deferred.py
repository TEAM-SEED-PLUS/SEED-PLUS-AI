import unittest

from indicator_engine import calculate_indicators
from llm_feed_writer import build_llm_context
from structural_risk_provider import (
    ACTIVATION_REQUIREMENTS, HistoricalOAClosureProvider, production_structural_risk_provider,
)
from v1_config import STRUCTURAL_CLOSURE_RISK_ENABLED, STRUCTURAL_CLOSURE_RISK_POLICY


def base_data():
    empty = {"count": 0, "items": []}
    return {"weather": {}, "special_day": empty, "festival": empty, "event": empty,
            "performance": empty, "sports": empty, "footfall": {}}


class StructuralRiskDeferredTests(unittest.TestCase):
    def test_structural_flag_is_false_and_provider_is_none(self):
        self.assertFalse(STRUCTURAL_CLOSURE_RISK_ENABLED)
        self.assertIsNone(production_structural_risk_provider())

    def test_policy_is_deferred_and_candidate_not_applied(self):
        self.assertEqual(STRUCTURAL_CLOSURE_RISK_POLICY["status"], "deferred")
        self.assertFalse(STRUCTURAL_CLOSURE_RISK_POLICY["production_applied"])

    def test_historical_snapshot_presence_does_not_change_risk(self):
        data = base_data()
        data["structural_risk"] = {"pooled_percentile": 100, "source_status": "ok"}
        result = calculate_indicators(data)
        self.assertEqual(result.operational_risk, 20)
        self.assertEqual(result.contribution_map["structural_risk"]["applied_contribution"], 0)

    def test_kosis_snapshot_presence_does_not_change_risk(self):
        data = base_data()
        data["kosis"] = {"enterprise_death_rate_pct": 100, "source_status": "ok"}
        self.assertEqual(calculate_indicators(data).operational_risk, 20)

    def test_existing_weather_content_special_day_regression(self):
        data = base_data()
        data["weather"] = {"summary": "비", "precipitation_mm": 5}
        data["special_day"] = {"count": 1, "holiday_count": 1, "items": []}
        data["festival"] = {"count": 1, "items": [{}]}
        self.assertEqual(calculate_indicators(data).operational_risk, 48)

    def test_llm_production_context_has_no_closure_claim(self):
        data = base_data(); result = calculate_indicators(data)
        context = build_llm_context({}, data, result)
        text = str(context)
        self.assertNotIn("structural_risk", text)
        self.assertNotIn("폐업률이 높아 위험", text)
        self.assertNotIn("KOSIS 기반 자치구 위험", text)

    def test_historical_candidate_remains_queryable_for_diagnostics(self):
        provider = HistoricalOAClosureProvider()
        value = provider.get_district_closure_rate("강남구", "2025Q2_2026Q1")
        self.assertAlmostEqual(value, 2.2939683871759446)
        self.assertFalse(provider.get_source_metadata()["production_applied"])

    def test_activation_gate_is_complete(self):
        self.assertEqual(len(ACTIVATION_REQUIREMENTS), 7)


if __name__ == "__main__": unittest.main()
