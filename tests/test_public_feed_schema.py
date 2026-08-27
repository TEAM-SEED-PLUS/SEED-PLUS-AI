import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from feed_renderer import build_market_feed_card
from llm_feed_writer import LLMFeedError
from public_feed_schema import SCHEMA_VERSION, generate_public_market_feed, serialize_public_feed
from v1_final_qa import replay_raw


class PublicFeedSchemaTests(unittest.TestCase):
    def _internal(self, district="강남구", date="2026-08-22", time="21:25"):
        with patch("market_feed_pipeline._get_all_city_info", side_effect=lambda **kw: replay_raw(kw["district"], kw["date_str"], kw["time_str"])), \
             patch.dict(os.environ, {}, clear=True), patch("llm_feed_writer.OPENAI_API_KEY", ""):
            from market_feed_pipeline import generate_market_feed
            return generate_market_feed(district, date, time)

    def test_required_fields_version_and_internal_data_hidden(self):
        public = serialize_public_feed(self._internal(), generated_at="2026-08-23T18:00:00+09:00")
        self.assertEqual(public["schema_version"], SCHEMA_VERSION)
        self.assertEqual(set(public), {"schema_version","query","opportunity_score","market_weather",
            "indicators","decision_tags","narrative","data_quality","sources","generated_at"})
        text=json.dumps(public, ensure_ascii=False).lower()
        for forbidden in ("raw_data","normalized_data","contribution_map","api_key","servicekey","exception"):
            self.assertNotIn(forbidden, text)

    def test_no_key_is_same_schema_and_rule_fallback(self):
        internal=self._internal(); public=serialize_public_feed(internal)
        self.assertEqual(public["narrative"]["generation_mode"], "rule_fallback")
        self.assertTrue(public["narrative"]["judgement_sentence"])
        self.assertTrue(public["narrative"]["basis_sentence"])
        self.assertTrue(public["narrative"]["recommended_actions"])

    def test_llm_success_and_failure_keep_shape(self):
        internal=self._internal(); fallback=serialize_public_feed(internal, generated_at="fixed")
        def llm_success(**kw):
            return build_market_feed_card(kw["query"], kw["normalized_data"], kw["indicators"]), "hybrid_llm", None
        with patch("market_feed_pipeline._get_all_city_info", return_value=replay_raw("강남구","2026-08-22","21:25")), \
             patch("market_feed_pipeline.generate_hybrid_market_feed_card", side_effect=llm_success):
            from market_feed_pipeline import generate_market_feed
            success=generate_market_feed("강남구","2026-08-22","21:25")
        hybrid=serialize_public_feed(success, generated_at="fixed")
        self.assertEqual(set(fallback), set(hybrid))
        self.assertEqual(hybrid["narrative"]["generation_mode"], "hybrid_llm")
        self.assertEqual(fallback["indicators"], hybrid["indicators"])
        self.assertEqual(fallback["market_weather"], hybrid["market_weather"])
        self.assertEqual(fallback["decision_tags"], hybrid["decision_tags"])
        with patch("market_feed_pipeline._get_all_city_info", return_value=replay_raw("강남구","2026-08-22","21:25")), \
             patch("market_feed_pipeline.generate_hybrid_market_feed_card", side_effect=LLMFeedError("failure")):
            failed=generate_market_feed("강남구","2026-08-22","21:25")
        failed_public=serialize_public_feed(failed, generated_at="fixed")
        self.assertEqual(set(fallback), set(failed_public))
        self.assertEqual(failed_public["narrative"]["generation_mode"], "rule_fallback")

    def test_scores_weather_and_tags_are_immutable_projection(self):
        internal=self._internal(); public=serialize_public_feed(internal)
        self.assertEqual(public["opportunity_score"], internal["scores"]["opportunity_score"])
        self.assertEqual(public["indicators"], {k:internal["scores"][k] for k in (
            "inflow_pressure","spending_intent","competition_pressure","operational_risk")})
        self.assertEqual(public["market_weather"]["grade"], internal["card"]["market_weather"])
        self.assertEqual(public["decision_tags"], internal["decision_tags"])

    def test_deep_night_metadata(self):
        public=serialize_public_feed(self._internal(date="2026-08-23",time="05:59"))
        self.assertEqual(public["data_quality"]["score_context"]["basis"], "previous_evening_reference")
        self.assertEqual(public["data_quality"]["score_context"]["reference_date"], "2026-08-22")
        self.assertIn("심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)", public["data_quality"]["badges"])

    def test_data_insufficient_and_structural_risk_not_exposed(self):
        public=serialize_public_feed(self._internal(district="중랑구"))
        self.assertTrue(public["data_quality"]["data_insufficient"])
        self.assertTrue(public["data_quality"]["fallback_sources"])
        self.assertNotIn("structural", json.dumps(public, ensure_ascii=False).lower())
        self.assertNotIn("kosis", json.dumps(public, ensure_ascii=False).lower())

    def test_validated_snapshot_quality_categories_are_disjoint(self):
        public = serialize_public_feed(self._internal())
        quality = public["data_quality"]
        self.assertEqual(quality["fallback_sources"], ["consumption_baseline"])
        self.assertEqual(quality["stale_sources"], ["tourism"])
        self.assertEqual(quality["skipped_sources"], ["realtime_commerce"])
        self.assertEqual(quality["failed_sources"], [])
        self.assertEqual(quality["empty_sources"], [])
        self.assertEqual(quality["status"], "partial")
        self.assertIn("weather", quality["no_data_sources"])
        self.assertIn("competition_sdot", quality["no_data_sources"])

    def test_successful_empty_content_and_special_day_are_not_no_data(self):
        internal = deepcopy(self._internal())
        for name in ("festival", "event", "performance", "sports", "special_day"):
            internal["source_status"][name] = {"status": "empty", "source": name}
            internal["normalized_data"][name]["source_status"] = {"status": "empty", "source": name}
            internal["normalized_data"][name]["count"] = 0
        quality = serialize_public_feed(internal)["data_quality"]
        expected = {"content.festival", "content.event", "content.performance",
                    "content.sports", "special_day"}
        self.assertTrue(expected <= set(quality["empty_sources"]))
        self.assertFalse(expected & set(quality["no_data_sources"]))
        self.assertFalse(expected & set(quality["fallback_sources"]))

    def test_failed_source_is_only_in_failed_sources(self):
        internal = deepcopy(self._internal())
        internal["source_status"]["weather"] = {"status": "failed", "source": "weather"}
        quality = serialize_public_feed(internal)["data_quality"]
        self.assertIn("weather", quality["failed_sources"])
        self.assertNotIn("weather", quality["fallback_sources"])
        self.assertNotIn("weather", quality["no_data_sources"])

    def test_public_generator_uses_existing_pipeline(self):
        with patch("market_feed_pipeline._get_all_city_info", return_value=replay_raw("강남구","2026-08-22","21:25")), \
             patch.dict(os.environ, {}, clear=True), patch("llm_feed_writer.OPENAI_API_KEY", ""):
            public=generate_public_market_feed("강남구","2026-08-22","21:25")
        self.assertEqual(public["schema_version"], "1.0")
        self.assertNotIn("raw_data", public)


if __name__ == "__main__": unittest.main()
