import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from public_feed_schema import serialize_public_feed


def sample_feed():
    return serialize_public_feed({
        "query": {"district": "강남구", "date": "2026-08-22", "time": "21:25", "time_band": "저녁"},
        "scores": {"opportunity_score": 52, "inflow_pressure": 48, "spending_intent": 30,
                   "competition_pressure": 29, "operational_risk": 20},
        "card": {"market_weather": "흐림", "weather_icon": "☁️", "judgement_sentence": "판단",
                 "basis_sentence": "근거", "recommended_actions": ["액션"]},
        "decision_tags": ["관망 권장"], "generation_mode": "rule_fallback",
        "source_status": {}, "normalized_data": {}, "score_context": {"basis": "requested_time"},
        "data_insufficient": False,
    }, generated_at="2026-08-23T18:00:00+09:00")


class FastAPIAppTests(unittest.TestCase):
    client = TestClient(app)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    @patch("app.main.generate_public_market_feed", side_effect=lambda **_: sample_feed())
    def test_valid_district_calls_pipeline(self, generate):
        response = self.client.get("/api/v1/weather-feeds", params={"district": "강남구"})
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(district="강남구", date_str=None, time_str=None)

    def test_invalid_district(self):
        response = self.client.get("/api/v1/weather-feeds", params={"district": "부산진구"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_date(self):
        response = self.client.get("/api/v1/weather-feeds", params={"district": "강남구", "date": "2026-02-30"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_time(self):
        response = self.client.get("/api/v1/weather-feeds", params={"district": "강남구", "time": "25:00"})
        self.assertEqual(response.status_code, 422)

    @patch("app.main.generate_public_market_feed", side_effect=lambda **_: sample_feed())
    def test_time_band_representative_times(self, generate):
        expected = {"심야": "03:00", "아침": "09:00", "점심": "14:00",
                    "오후": "18:00", "저녁": "21:00"}
        for band, representative in expected.items():
            response = self.client.get("/api/v1/weather-feeds", params={
                "district": "강남구", "date": "2026-09-12", "time_band": band})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(generate.call_args.kwargs["time_str"], representative)

    @patch("app.main.generate_public_market_feed", side_effect=lambda **_: sample_feed())
    def test_matching_time_and_band(self, generate):
        response = self.client.get("/api/v1/weather-feeds", params={
            "district": "강남구", "time": "10:38", "time_band": "아침"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.kwargs["time_str"], "10:38")

    def test_mismatching_or_invalid_time_band(self):
        mismatch = self.client.get("/api/v1/weather-feeds", params={
            "district": "강남구", "time": "10:38", "time_band": "저녁"})
        invalid = self.client.get("/api/v1/weather-feeds", params={
            "district": "강남구", "time_band": "새벽"})
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(invalid.status_code, 422)

    @patch("app.main.load_weather_overview")
    def test_overview_reads_snapshot_only(self, loader):
        loader.return_value = {"schema_version": "1.0",
            "query": {"date": "2026-09-12", "time": "09:00", "time_band": "아침"},
            "status": "no_data", "districts": [],
            "generated_at": "2026-09-12T09:00:00+09:00", "source_time": None,
            "age_minutes": None, "fresh_ttl_minutes": 10, "is_fresh": False}
        response = self.client.get("/api/v1/weather-feeds/overview", params={
            "date": "2026-09-12", "time": "10:38", "time_band": "아침"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query"]["time"], "09:00")
        loader.assert_called_once_with("2026-09-12", "10:38", "아침")

    def test_matching_detail_preserves_actual_request_time(self):
        def generated(**kwargs):
            value = sample_feed()
            value["query"]["time"] = kwargs["time_str"]
            value["query"]["time_band"] = "아침"
            return value
        with patch("app.main.generate_public_market_feed", side_effect=generated):
            response = self.client.get("/api/v1/weather-feeds", params={
                "district": "강남구", "time": "10:38", "time_band": "아침"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query"]["time"], "10:38")

    def test_overview_time_band_mismatch(self):
        response = self.client.get("/api/v1/weather-feeds/overview", params={
            "date": "2026-09-12", "time": "10:38", "time_band": "저녁"})
        self.assertEqual(response.status_code, 422)

    @patch("app.main.generate_public_market_feed", side_effect=lambda **_: sample_feed())
    def test_public_feed_is_returned_without_wrapper(self, _generate):
        response = self.client.get("/api/v1/weather-feeds", params={
            "district": "강남구", "date": "2026-08-22", "time": "21:25"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), sample_feed())
        self.assertNotIn("data", response.json())

    def test_secret_is_not_exposed_by_internal_error(self):
        secret = "test-secret-api-key"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}), \
             patch("app.main.generate_public_market_feed", side_effect=RuntimeError(f"upstream: {secret}")):
            response = self.client.get("/api/v1/weather-feeds", params={"district": "강남구"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(secret, json.dumps(response.json()))


if __name__ == "__main__":
    unittest.main()
