import os
import unittest
from unittest.mock import patch

from common import build_query_context
from indicator_engine import (
    calculate_indicators,
    classify_market_weather,
    count_qualifying_content,
    determine_decision_tags,
)
from market_feed_pipeline import generate_market_feed
from llm_feed_writer import generate_hybrid_market_feed_card
from source_status import collect_with_status
from v1_config import OPPORTUNITY_WEIGHTS


def tags(**overrides):
    values = dict(
        inflow=50,
        spending=50,
        competition=50,
        risk=50,
        opportunity=65,
        recommended_categories=[],
        qualifying_content_count=0,
    )
    values.update(overrides)
    return determine_decision_tags(**values)


def empty_normalized():
    empty_content = {"count": 0, "items": []}
    return {
        "query": {"district": "송파구", "time_band": "아침"},
        "weather": {},
        "footfall": {"summary": {}, "items": []},
        "special_day": {"count": 0, "items": []},
        "festival": dict(empty_content),
        "event": dict(empty_content),
        "performance": dict(empty_content),
        "sports": dict(empty_content),
        "overview": {"content_tag_counts": {}},
        "source_status": {},
    }


class V1BoundaryTests(unittest.TestCase):
    def test_opportunity_weights(self):
        self.assertEqual(
            OPPORTUNITY_WEIGHTS,
            {"inflow": 0.35, "spending": 0.30, "inverse_competition": 0.15, "inverse_risk": 0.20},
        )

    def test_weather_boundaries(self):
        expected = {79: "구름", 80: "맑음", 64: "흐림", 65: "구름", 49: "비", 50: "흐림", 34: "폭풍", 35: "비"}
        self.assertEqual({score: classify_market_weather(score) for score in expected}, expected)

    def test_spread_29_and_30(self):
        common = dict(spending=62, competition=40, risk=40, opportunity=65, recommended_categories=["카페"], qualifying_content_count=2)
        self.assertNotIn("특정 업종만 유리", tags(inflow=69, **common))
        self.assertIn("특정 업종만 유리", tags(inflow=70, **common))

    def test_event_is_included_in_qualifying_content_count(self):
        data = empty_normalized()
        data["festival"]["count"] = 1
        self.assertEqual(count_qualifying_content(data), 1)
        data["event"]["count"] = 1
        count = count_qualifying_content(data)
        self.assertEqual(count, 2)
        result = tags(
            inflow=70, spending=62, competition=40, risk=40, opportunity=65,
            recommended_categories=["카페"], qualifying_content_count=count,
        )
        self.assertIn("특정 업종만 유리", result)

    def test_time_boundaries(self):
        expected = {
            "00:00": "심야", "05:59": "심야", "06:00": "아침", "11:59": "아침", "12:00": "점심",
            "16:59": "점심", "17:00": "오후", "19:59": "오후", "20:00": "저녁", "23:59": "저녁",
        }
        actual = {value: build_query_context("송파구", "2026-08-22", value).time_band for value in expected}
        self.assertEqual(actual, expected)

    def test_risk_54_and_55(self):
        self.assertIn("진입 유리", tags(risk=54, competition=64))
        self.assertNotIn("리스크 주의", tags(risk=54))
        self.assertNotIn("진입 유리", tags(risk=55, competition=64))
        self.assertIn("리스크 주의", tags(risk=55))

    def test_competition_boundaries(self):
        self.assertIn("진입 유리", tags(competition=64, risk=54))
        self.assertNotIn("진입 유리", tags(competition=65, risk=54))
        self.assertNotIn("과열 상태", tags(competition=69))
        self.assertIn("과열 상태", tags(competition=70))

    def test_spending_61_and_62(self):
        self.assertNotIn("기회 구간", tags(inflow=68, spending=61))
        self.assertIn("기회 구간", tags(inflow=68, spending=62))

    def test_no_watch_when_only_entry_fails_but_opportunity_zone_matches(self):
        result = tags(inflow=68, spending=62, competition=65, risk=54, opportunity=65)
        self.assertNotIn("진입 유리", result)
        self.assertIn("기회 구간", result)
        self.assertNotIn("관망 권장", result)


class V1PolicyTests(unittest.TestCase):
    @staticmethod
    def _raw_data(date, time, band):
        statuses = {}
        result = {"query": {"district": "송파구", "date": date, "time": time, "time_band": band}}
        for name in ("festival", "event", "performance", "sports"):
            status = {"status": "no_data", "source": name}
            result[name] = {"count": 0, "items": [], "source_status": status}
            statuses[name] = status
        for name in ("footfall", "special_day"):
            status = {"status": "no_data", "source": name}
            result[name] = {"count": 0, "items": [], "summary": {}, "source_status": status}
            statuses[name] = status
        status = {"status": "no_data", "source": "weather"}
        result["weather"] = {"source_status": status}
        statuses["weather"] = status
        result["source_status"] = statuses
        return result

    def test_night_uses_only_previous_evening_for_scoring(self):
        current = self._raw_data("2026-08-22", "05:59", "심야")
        previous = self._raw_data("2026-08-21", "20:00", "저녁")
        fallback_card = {"district": "송파구", "indicators": {}, "decision_tags": [], "recommended_actions": []}
        with patch("market_feed_pipeline._get_all_city_info", side_effect=[current, previous]) as collect, patch(
            "market_feed_pipeline.calculate_indicators", wraps=calculate_indicators
        ) as calculate, patch(
            "market_feed_pipeline.generate_hybrid_market_feed_card",
            return_value=(fallback_card, "rule_fallback", None),
        ) as writer:
            result = generate_market_feed("송파구", "2026-08-22", "05:59")

        self.assertEqual(collect.call_count, 2)
        self.assertEqual(collect.call_args_list[1].kwargs["date_str"], "2026-08-21")
        self.assertEqual(collect.call_args_list[1].kwargs["time_str"], "20:00")
        self.assertEqual(calculate.call_count, 1)
        self.assertEqual(calculate.call_args.args[0]["query"]["date"], "2026-08-21")
        self.assertEqual(result["score_context"]["basis"], "previous_evening_reference")
        self.assertEqual(result["score_context"]["reference_date"], "2026-08-21")
        self.assertEqual(result["score_context"]["reference_time_band"], "저녁")
        self.assertEqual(result["score_context"]["reference_start"], "20:00")
        self.assertEqual(result["score_context"]["reference_end"], "24:00")
        self.assertEqual(result["score_context"]["single_time_sources"]["weather"], "20:00")
        self.assertEqual(result["notice"], "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)")
        self.assertEqual(result["card"]["score_label"], "직전 저녁 참고값")
        llm_query = writer.call_args.kwargs["query"]
        self.assertEqual(llm_query["score_context"]["basis"], "previous_evening_reference")
        self.assertEqual(llm_query["requested_query"]["time_band"], "심야")

    def test_partial_tourism_snapshot_keeps_feed_running_and_uses_common_shortage_state(self):
        raw = self._raw_data("2026-08-22", "12:00", "점심")
        partial = {
            "requested_month": "202608", "source_month": "202509", "age_months": 11,
            "source_status": "fallback", "snapshot_status": "partial", "district": "송파구",
            "metrics": {"stay_intensity": {"2101": {
                "raw_value": 70, "normalized_value": 70, "source_month": "202509",
                "source_status": "fallback", "fallback_reason": "requested_month_snapshot_missing",
            }}},
        }
        fallback_card = {"district": "송파구", "indicators": {}, "decision_tags": [], "recommended_actions": []}
        with patch("market_feed_pipeline._get_all_city_info", return_value=raw), patch(
            "market_feed_pipeline.get_cached_district_tourism_baseline", return_value=partial
        ), patch(
            "market_feed_pipeline.generate_hybrid_market_feed_card",
            return_value=(fallback_card, "rule_fallback", None),
        ):
            result = generate_market_feed("송파구", "2026-08-22", "12:00")

        self.assertIsInstance(result["scores"]["opportunity_score"], int)
        self.assertTrue(result["data_insufficient"])
        self.assertEqual(result["source_status"]["tourism_baseline"]["status"], "no_data")
        self.assertEqual(result["source_status"]["tourism_baseline"]["source_month"], "202509")

    def test_failed_source_keeps_base_and_drops_bonus(self):
        data = empty_normalized()
        data["weather"] = {"summary": "맑음", "temperature_c": 20}
        data["source_status"] = {"weather": {"status": "failed"}}
        result = calculate_indicators(data)
        self.assertEqual(result.inflow_pressure, 24)
        self.assertEqual(result.spending_intent, 24)
        self.assertEqual(result.operational_risk, 20)

    def test_collection_distinguishes_no_data_and_failed(self):
        no_data = collect_with_status("festival", lambda: {"count": 0, "items": []})
        failed = collect_with_status("festival", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual(no_data["source_status"]["status"], "no_data")
        self.assertEqual(failed["source_status"]["status"], "failed")

    def test_missing_api_key_reports_rule_fallback(self):
        indicators = calculate_indicators(empty_normalized())
        with patch.dict(os.environ, {}, clear=True), patch("llm_feed_writer.OPENAI_API_KEY", ""):
            _, mode, error = generate_hybrid_market_feed_card(
                {"district": "송파구"}, empty_normalized(), indicators, return_generation_mode=True
            )
        self.assertEqual(mode, "rule_fallback")
        self.assertIn("OPENAI_API_KEY", error)


if __name__ == "__main__":
    unittest.main()
