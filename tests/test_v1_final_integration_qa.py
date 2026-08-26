import unittest
from unittest.mock import patch

from common import build_query_context
from indicator_engine import calculate_indicators, classify_market_weather
from market_feed_pipeline import generate_market_feed
from normalized_city_data import normalize_city_data
from v1_final_qa import feature_flags, replay_raw, run


class V1FinalIntegrationQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = run()

    def test_25_district_final_path_and_reconstruction(self):
        self.assertEqual(len(self.qa["districts"]), 25)
        self.assertEqual(self.qa["score_reconstruction"]["mismatch_count"], 0)

    def test_feature_flag_matrix(self):
        self.assertEqual(feature_flags(), {"tourism": True, "oa21285_footfall": True,
            "commercial_store_competition": True, "consumption_hybrid": True,
            "structural_closure_risk": False})

    def test_weather_no_data_does_not_score_as_clear(self):
        data = normalize_city_data(replay_raw("강남구", "2026-08-23", "16:00"))
        data["consumption_hybrid"] = {"baseline": {"source_status": "failed"},
                                      "realtime": {"source_status": "no_data"}}
        result = calculate_indicators(data)
        self.assertEqual(result.contribution_map["weather"]["inflow_bonus"], 0)
        self.assertEqual(result.contribution_map["weather"]["spending_bonus"], 0)

    def test_time_boundaries(self):
        expected = {"00:00":"심야","05:59":"심야","06:00":"아침","11:59":"아침",
                    "12:00":"점심","16:59":"점심","17:00":"오후","19:59":"오후",
                    "20:00":"저녁","23:59":"저녁"}
        for time, band in expected.items():
            self.assertEqual(build_query_context("강남구", "2026-08-23", time).time_band, band)

    def test_weather_boundaries(self):
        expected = {34:"폭풍",35:"비",49:"비",50:"흐림",64:"흐림",65:"구름",79:"구름",80:"맑음"}
        self.assertEqual({x: classify_market_weather(x) for x in expected}, expected)

    def test_deep_night_final_path(self):
        calls=[]
        def loader(**kw):
            calls.append((kw["date_str"], kw["time_str"]))
            return replay_raw(kw["district"], kw["date_str"], kw["time_str"])
        with patch("market_feed_pipeline._get_all_city_info", side_effect=loader):
            result=generate_market_feed("강남구","2026-08-23","05:59")
        self.assertEqual(calls, [("2026-08-23","05:59"),("2026-08-22","20:00")])
        self.assertEqual(result["score_context"]["basis"], "previous_evening_reference")
        self.assertEqual(result["notice"], "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)")
        self.assertIsNotNone(result["reference"])

    def test_structural_deferred(self):
        for row in self.qa["districts"]:
            self.assertEqual(row["contributions"]["risk"]["structural"], 0)

    def test_no_oa_consumption_double_count(self):
        for row in self.qa["districts"]:
            s=row["contributions"]["spending"]
            if s["consumption_baseline"] or s["realtime_commerce"]:
                self.assertEqual(s["oa_legacy_fallback"], 0)

    def test_total_external_failure_still_builds_feed(self):
        failed={"data":None,"requested_quarter":"2026Q3","source_quarter":None,"source_status":"failed","age_quarters":None}
        with patch("market_feed_pipeline._get_all_city_info", return_value=replay_raw("중랑구","2026-08-23","16:00")), \
             patch("commercial_store_snapshot.load_scoring_snapshot", return_value=failed):
            result=generate_market_feed("중랑구","2026-08-23","16:00")
        self.assertTrue(result["feed_text"])
        self.assertEqual(result["scores"]["operational_risk"], 20)
        self.assertEqual(result["scores"]["competition_pressure"], 18)

    def test_llm_immutable_fields_rule_fallback(self):
        with patch("market_feed_pipeline._get_all_city_info", return_value=replay_raw("강남구","2026-08-22","21:25")):
            result=generate_market_feed("강남구","2026-08-22","21:25")
        self.assertEqual(result["card"]["indicators"], {
            "유입 압력": result["scores"]["inflow_pressure"],
            "소비 의도": result["scores"]["spending_intent"],
            "경쟁 압박": result["scores"]["competition_pressure"],
            "운영 리스크": result["scores"]["operational_risk"],
        })
        self.assertTrue(set(result["card"]["decision_tags"]).issubset(result["decision_tags"]))


if __name__ == "__main__":
    unittest.main()
