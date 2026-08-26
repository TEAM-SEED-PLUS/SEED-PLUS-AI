import os
import unittest
from unittest.mock import patch

from indicator_engine import calculate_indicators, calculate_tourism_baseline_contribution
from llm_feed_writer import build_llm_context, generate_hybrid_market_feed_card


def empty_normalized():
    empty_content = {"count": 0, "items": []}
    return {
        "query": {"district": "강남구", "date": "2025-09-15", "time_band": "점심"},
        "weather": {}, "footfall": {"summary": {}, "items": []},
        "special_day": {"count": 0, "items": []},
        "festival": dict(empty_content), "event": dict(empty_content),
        "performance": dict(empty_content), "sports": dict(empty_content),
        "overview": {"content_tag_counts": {}}, "source_status": {},
    }


CODE_GROUP = {
    **{code: "stay_intensity" for code in ("2101", "2102", "2103", "2104", "2105")},
    **{code: "spending_intensity" for code in ("2201", "2202", "2203")},
    "3302": "international_diversity",
    **{code: "cultural_resource_demand" for code in ("1201", "1202", "1203", "1204", "1205")},
    "1106": "service_demand",
    **{code: "spending_diversity" for code in ("3201", "3202", "3203", "3204", "3205", "3206", "3207")},
}


def tourism_data(values=None, *, requested="202511", snapshot_status="complete", statuses=None, source_months=None):
    values = values or {}
    statuses = statuses or {}
    source_months = source_months or {}
    metrics = {}
    for code, value in values.items():
        group = CODE_GROUP[code]
        metrics.setdefault(group, {})[code] = {
            "indicator_name": f"지표 {code}",
            "raw_value": value,
            "normalized_value": value,
            "requested_month": requested,
            "source_month": source_months.get(code, requested),
            "source_status": statuses.get(code, "ok"),
        }
    return {
        "tourism_baseline": {
            "requested_month": requested,
            "snapshot_status": snapshot_status,
            "district": "강남구",
            "metrics": metrics,
        }
    }


def all_values(value):
    return {code: value for code in CODE_GROUP}


class TourismContributionTests(unittest.TestCase):
    def test_group_composites_and_single_components(self):
        values = {
            "2101": 10, "2102": 20, "2103": 30, "2104": 40, "2105": 50,
            "1201": 20, "1202": 30, "1203": 40, "1204": 50, "1205": 60,
            "2201": 30, "2202": 60, "2203": 90, "3302": 70, "1106": 80,
            "3201": 10, "3202": 20, "3203": 30, "3204": 40,
            "3205": 50, "3206": 60, "3207": 70,
        }
        result = calculate_tourism_baseline_contribution(tourism_data(values))
        self.assertEqual(result["inflow"]["stay_score"], 30.0)
        self.assertEqual(result["inflow"]["culture_score"], 40.0)
        self.assertEqual(result["inflow"]["foreign_visitor_score"], 70.0)
        self.assertEqual(result["spending"]["spending_score"], 60.0)
        self.assertEqual(result["spending"]["food_score"], 80.0)
        self.assertEqual(result["spending"]["age_spending_diversity_score"], 40.0)
        self.assertEqual(result["inflow"]["index"], 44.0)
        self.assertEqual(result["spending"]["index"], 64.0)

    def test_groups_are_equally_weighted_not_flattened(self):
        values = {
            "2101": 100, "2102": 100, "2103": 100, "2104": 100, "2105": 100,
            "3302": 0,
            "1201": 0, "1202": 0, "1203": 0, "1204": 0, "1205": 0,
        }
        result = calculate_tourism_baseline_contribution(tourism_data(values))
        self.assertEqual(result["inflow"]["index"], 50.0)
        self.assertNotEqual(result["inflow"]["index"], round(500 / 11, 2))

    def test_bonus_boundaries(self):
        for index, expected in ((0, 0), (50, 5), (100, 10)):
            with self.subTest(index=index):
                result = calculate_tourism_baseline_contribution(tourism_data(all_values(index)))
                self.assertEqual(result["inflow"]["bonus"], expected)
                self.assertEqual(result["spending"]["bonus"], expected)

    def test_partial_components_do_not_renormalize_weights(self):
        result = calculate_tourism_baseline_contribution(tourism_data({"2101": 60, "1201": 80}, snapshot_status="partial"))
        self.assertEqual(result["inflow"]["stay_score"], 60.0)
        self.assertIsNone(result["inflow"]["foreign_visitor_score"])
        self.assertEqual(result["inflow"]["culture_score"], 80.0)
        self.assertEqual(result["inflow"]["index"], 46.0)
        self.assertEqual(result["source_status"], "partial")

    def test_all_components_missing_means_no_bonus_not_zero_demand(self):
        result = calculate_tourism_baseline_contribution(tourism_data({}, snapshot_status="partial"))
        self.assertIsNone(result["inflow"]["index"])
        self.assertIsNone(result["spending"]["index"])
        self.assertEqual(result["inflow"]["bonus"], 0)
        self.assertEqual(result["spending"]["bonus"], 0)
        self.assertEqual(result["source_status"], "no_data")

    def test_fallback_two_months_is_usable_and_source_month_preserved(self):
        result = calculate_tourism_baseline_contribution(tourism_data(
            {"3302": 80}, statuses={"3302": "fallback"}, source_months={"3302": "202509"}
        ))
        component = result["inflow"]["components"]["3302"]
        self.assertEqual(result["inflow"]["index"], 24.0)
        self.assertEqual(component["source_month"], "202509")
        self.assertEqual(component["source_status"], "fallback")

    def test_fallback_three_months_is_usable(self):
        result = calculate_tourism_baseline_contribution(tourism_data(
            {"3302": 80}, statuses={"3302": "fallback"}, source_months={"3302": "202508"}
        ))
        self.assertEqual(result["inflow"]["index"], 24.0)
        self.assertEqual(result["inflow"]["bonus"], 2.4)
        self.assertEqual(result["inflow"]["components"]["3302"]["source_status"], "fallback")
        self.assertEqual(result["inflow"]["components"]["3302"]["age_months"], 3)

    def test_fallback_four_months_is_excluded_and_insufficient(self):
        result = calculate_tourism_baseline_contribution(tourism_data(
            {"3302": 80}, statuses={"3302": "fallback"}, source_months={"3302": "202507"}
        ))
        component = result["inflow"]["components"]["3302"]
        self.assertIsNone(result["inflow"]["index"])
        self.assertEqual(result["inflow"]["bonus"], 0)
        self.assertEqual(result["source_status"], "no_data")
        self.assertEqual(component["source_month"], "202507")
        self.assertEqual(component["age_months"], 4)
        self.assertEqual(component["fallback_reason"], "fallback_age_exceeded")

    def test_feature_flag_true_applies_official_v1_scoring(self):
        before_data = empty_normalized()
        after_data = empty_normalized()
        after_data.update(tourism_data(all_values(100)))
        before = calculate_indicators(before_data)
        after = calculate_indicators(after_data)
        self.assertEqual(after.inflow_pressure, before.inflow_pressure + 10)
        self.assertEqual(after.spending_intent, before.spending_intent + 10)
        self.assertEqual(after.competition_pressure, before.competition_pressure)
        self.assertEqual(after.operational_risk, before.operational_risk)
        self.assertTrue(after.contribution_map["tourism_baseline"]["scoring_enabled"])
        self.assertEqual(after.contribution_map["tourism_baseline"]["policy_status"], "official_v1")
        self.assertEqual(after.contribution_map["tourism_baseline"]["inflow"]["applied_bonus"], 10)

    def test_tourism_scoring_applies_only_to_inflow_and_spending(self):
        before = calculate_indicators(empty_normalized())
        data = empty_normalized()
        data.update(tourism_data(all_values(100)))
        after = calculate_indicators(data)
        self.assertEqual(after.inflow_pressure, before.inflow_pressure + 10)
        self.assertEqual(after.spending_intent, before.spending_intent + 10)
        self.assertEqual(after.competition_pressure, before.competition_pressure)
        self.assertEqual(after.operational_risk, before.operational_risk)
        self.assertEqual(after.contribution_map["tourism_baseline"]["inflow"]["applied_bonus"], 10)

    def test_final_inflow_and_spending_are_clamped(self):
        data = empty_normalized()
        data.update(tourism_data(all_values(100)))
        data["festival"] = {"count": 50, "items": [{"title": "축제"}] * 50}
        data["event"] = {"count": 50, "items": [{"title": "행사"}] * 50}
        data["performance"] = {"count": 50, "items": [{"title": "공연"}] * 50}
        data["sports"] = {"count": 50, "items": [{"title": "경기"}] * 50}
        data["footfall"] = {"summary": {"visitor_avg_in_band": 10**9, "visitor_max_in_band": 10**9}}
        result = calculate_indicators(data)
        self.assertLessEqual(result.inflow_pressure, 100)
        self.assertLessEqual(result.spending_intent, 100)

    def test_contribution_map_and_monthly_evidence_are_traceable(self):
        data = empty_normalized()
        data.update(tourism_data(all_values(50), requested="202509"))
        result = calculate_indicators(data)
        tourism = result.contribution_map["tourism_baseline"]
        self.assertEqual(tourism["requested_month"], "202509")
        self.assertIn("2101", tourism["inflow"]["components"])
        self.assertEqual(tourism["inflow"]["components"]["2101"]["raw_value"], 50)
        self.assertIn("2025년 9월 월간 관광 기저", " ".join(result.evidence["reason_summary"]))

    def test_llm_context_explicitly_marks_monthly_relative_data(self):
        data = empty_normalized()
        data.update(tourism_data(all_values(50), requested="202509"))
        indicators = calculate_indicators(data)
        context = build_llm_context({"district": "강남구"}, data, indicators)
        tourism = context["tourism_baseline"]
        self.assertIn("월간 자치구 상대지표", tourism["data_semantics"])
        self.assertEqual(tourism["requested_month"], "202509")
        self.assertEqual(tourism["tourism_inflow_index"], 50.0)

    def test_existing_rule_fallback_still_works(self):
        data = empty_normalized()
        data.update(tourism_data(all_values(50)))
        indicators = calculate_indicators(data)
        with patch.dict(os.environ, {}, clear=True), patch("llm_feed_writer.OPENAI_API_KEY", ""):
            card, mode, _ = generate_hybrid_market_feed_card(
                {"district": "강남구"}, data, indicators, return_generation_mode=True
            )
        self.assertEqual(mode, "rule_fallback")
        self.assertEqual(card["indicators"]["유입 압력"], indicators.inflow_pressure)


if __name__ == "__main__":
    unittest.main()
