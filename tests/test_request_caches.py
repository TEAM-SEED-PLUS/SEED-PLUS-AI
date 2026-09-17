import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from common import SEOUL_TZ
from footfall_api import _fetch_page, get_sdot_visitor_data
from performance_cache import get_cached_performances
from sports_cache import get_cached_sports
from public_feed_schema import serialize_public_feed
from source_status import collect_with_status


def snapshot(items, *, minutes_old=0, status="ok"):
    generated = datetime.now(SEOUL_TZ) - timedelta(minutes=minutes_old)
    return {"generated_at": generated.isoformat(), "source_status": status,
            "item_count": len(items), "items": items}


class SdotStatusTests(unittest.TestCase):
    @patch("footfall_api.requests.get")
    def test_info_200_is_empty_and_stops_before_english_retry(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
        get.return_value = response
        value = get_sdot_visitor_data("not-a-secret", "강남구", "2026-09-12", allow_english_fallback=False)
        self.assertTrue(value.empty)
        self.assertEqual(get.call_count, 1)

    @patch("footfall_api.requests.get", side_effect=requests.ConnectionError("down"))
    def test_transport_exception_is_failure(self, _get):
        value = collect_with_status("footfall", lambda: _fetch_page("not-a-secret", "강남구", 1, 1))
        self.assertEqual(value["source_status"]["status"], "failed")


class CacheLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_sports_hit_filters_district_and_band_without_playwright(self):
        self.write("2026-09-12.json", snapshot([
            {"sport": "Baseball", "league": "KBO", "date": "2026-09-12", "time": "18:30", "match": "A vs B", "stadium": "잠실야구장", "district": "송파구"},
            {"sport": "Baseball", "league": "KBO", "date": "2026-09-12", "time": "21:00", "match": "C vs D", "stadium": "잠실야구장", "district": "송파구"},
            {"sport": "Soccer", "league": "K League 1", "date": "2026-09-12", "time": "18:30", "match": "E vs F", "stadium": "서울월드컵경기장", "district": "마포구"},
        ]))
        value = get_cached_sports("송파구", "2026-09-12", "18:00", data_dir=self.root)
        self.assertEqual([x["match"] for x in value["items"]], ["A vs B"])
        self.assertIsNone(value["items"][0]["link_url"])

    def test_sports_miss_does_not_use_playwright(self):
        value = get_cached_sports("송파구", "2026-09-12", "18:00", data_dir=self.root)
        self.assertEqual(value["source_status"]["eligibility_reason"], "cache_missing")

    @patch("performance_api.KOPISClient", side_effect=AssertionError("must not call network"))
    def test_performance_hit_filters_district_and_band_without_kopis(self, _client):
        self.write("2026-09-12.json", snapshot([
            {"title": "저녁 공연", "place": "공연장", "district_from_address": "강남구", "schedule_text": "토요일 18:30"},
            {"title": "밤 공연", "place": "공연장", "district_from_address": "강남구", "schedule_text": "토요일 21:00"},
            {"title": "타구 공연", "place": "공연장", "district_from_address": "송파구", "schedule_text": "토요일 18:30"},
        ]))
        value = get_cached_performances("강남구", "2026-09-12", "18:00", data_dir=self.root)
        self.assertEqual([x["title"] for x in value["items"]], ["저녁 공연"])
        self.assertIsNone(value["items"][0]["link_url"])

    @patch("performance_api.KOPISClient", side_effect=AssertionError("must not call network"))
    def test_performance_miss_does_not_use_kopis(self, client):
        value = get_cached_performances("강남구", "2026-09-12", "18:00", data_dir=self.root)
        self.assertEqual(value["source_status"]["eligibility_reason"], "cache_missing")
        client.assert_not_called()

    def test_stale_snapshot_is_not_served(self):
        self.write("2026-09-12.json", snapshot([], minutes_old=61))
        value = get_cached_performances("강남구", "2026-09-12", "18:00", data_dir=self.root, ttl_minutes=60)
        self.assertEqual(value["items"], [])
        self.assertEqual(value["source_status"]["eligibility_reason"], "stale_cache")

    def test_stale_cache_reaches_public_quality_group(self):
        status = {"status": "no_data", "source": "performance", "eligibility_reason": "stale_cache", "age_minutes": 61}
        internal = {"query": {}, "scores": {}, "card": {}, "decision_tags": [], "data_insufficient": True,
                    "generation_mode": "rule_fallback", "score_context": {},
                    "source_status": {"performance": status},
                    "normalized_data": {"performance": {"count": 0}}}
        feed = serialize_public_feed(internal)
        self.assertIn("content.performance", feed["data_quality"]["stale_sources"])


class ParallelCollectionTests(unittest.TestCase):
    def test_one_live_failure_does_not_cancel_other_sources(self):
        good = lambda *args: {"count": 1, "items": [{"ok": True}]}
        bad = lambda *args: (_ for _ in ()).throw(RuntimeError("down"))
        no_oa = {"source_status": {"status": "no_data"}}
        with patch("integrated_city_info.get_weather", good), patch("integrated_city_info.get_festivals", good), \
             patch("integrated_city_info.get_events", bad), patch("integrated_city_info.get_special_day_info", good), \
             patch("integrated_city_info.get_cached_performances", return_value={"count": 0, "items": [], "source_status": {"status": "empty"}}), \
             patch("integrated_city_info.get_cached_sports", return_value={"count": 0, "items": [], "source_status": {"status": "empty"}}), \
             patch("integrated_city_info.load_available_oa_footfall", return_value=no_oa), \
             patch("integrated_city_info.resolve_indicator_footfall_sources", return_value={"count": 0, "items": [], "source_status": {"status": "no_data"}}):
            from integrated_city_info import get_all_city_info
            value = get_all_city_info("강남구", "2026-09-12", "18:00")
        self.assertEqual(value["event"]["source_status"]["status"], "failed")
        self.assertEqual(value["festival"]["source_status"]["status"], "ok")
        self.assertEqual(value["special_day"]["source_status"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
