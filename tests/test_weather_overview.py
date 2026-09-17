import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from common import DISTRICT_KO_TO_EN, SEOUL_TZ
from public_feed_schema import generate_public_market_feed
from v1_final_qa import replay_raw
from weather_overview_cache import load_weather_overview
from weather_overview_collector import (collect_active_weather_overview,
                                        collect_weather_overview)


class WeatherOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def feed(**kwargs):
        district = kwargs["district"]
        score = 50 + (len(district) % 7)
        return {"opportunity_score": score, "market_weather": {"grade": "구름", "emoji": "⛅"},
                "query": {"district": district}}

    def test_collector_writes_25_district_atomic_snapshot(self):
        result = collect_weather_overview("2026-09-12", "아침", data_dir=self.root,
                                          generator=self.feed)
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual(result["district_count"], 25)
        self.assertEqual(len(result["districts"]), 25)
        expected = {district: self.feed(district=district)["opportunity_score"]
                    for district in (row["district"] for row in result["districts"])}
        for row in result["districts"]:
            detail = self.feed(district=row["district"])
            self.assertEqual(row["opportunity_score"], expected[row["district"]])
            self.assertEqual(row["grade"], detail["market_weather"]["grade"])
            self.assertEqual(row["emoji"], detail["market_weather"]["emoji"])

    def test_all_districts_match_actual_production_projection(self):
        def replay(**kwargs):
            return replay_raw(kwargs["district"], kwargs["date_str"], kwargs["time_str"])
        with patch("market_feed_pipeline._get_all_city_info", side_effect=replay), \
             patch("llm_feed_writer.OPENAI_API_KEY", ""):
            snapshot = collect_weather_overview("2026-09-12", "아침", data_dir=self.root,
                                                generator=generate_public_market_feed)
            by_district = {row["district"]: row for row in snapshot["districts"]}
            self.assertEqual(set(by_district), set(DISTRICT_KO_TO_EN))
            for district in DISTRICT_KO_TO_EN:
                detail = generate_public_market_feed(district=district,
                                                     date_str="2026-09-12", time_str="09:00")
                self.assertEqual(by_district[district]["opportunity_score"], detail["opportunity_score"])
                self.assertEqual(by_district[district]["grade"], detail["market_weather"]["grade"])
                self.assertEqual(by_district[district]["emoji"], detail["market_weather"]["emoji"])
        self.assertFalse(list(self.root.glob("*.tmp")))
        loaded = load_weather_overview("2026-09-12", "09:00", "아침", data_dir=self.root)
        self.assertEqual(loaded["status"], "ok")
        self.assertEqual(len(loaded["districts"]), 25)

    def test_partial_and_failed_status(self):
        def partial(**kwargs):
            if kwargs["district"] == "강남구":
                raise RuntimeError("secret must not be serialized")
            return self.feed(**kwargs)
        result = collect_weather_overview("2026-09-12", "점심", data_dir=self.root,
                                          generator=partial)
        self.assertEqual(result["source_status"], "partial")
        self.assertEqual(result["failed_districts"], ["강남구"])
        self.assertNotIn("secret", str(result))
        failed = collect_weather_overview("2026-09-12", "저녁", data_dir=self.root,
                                          generator=lambda **_: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(failed["source_status"], "failed")
        no_data = collect_weather_overview("2026-09-12", "심야", data_dir=self.root,
                                           generator=lambda **_: {"opportunity_score": None,
                                                                  "market_weather": {}})
        self.assertEqual(no_data["source_status"], "no_data")

    def test_missing_snapshot_is_no_data_without_generation(self):
        result = load_weather_overview("2026-09-12", "17:25", "오후", data_dir=self.root)
        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["districts"], [])
        self.assertEqual(result["query"]["time"], "18:00")
        self.assertFalse(result["is_fresh"])
        self.assertEqual(result["fresh_ttl_minutes"], 10)

    def test_snapshot_freshness_ttl_and_stale_status(self):
        now = datetime(2026, 9, 12, 9, 30, tzinfo=SEOUL_TZ)
        with patch("weather_overview_collector.datetime") as mocked_datetime:
            mocked_datetime.strptime = datetime.strptime
            mocked_datetime.now.return_value = now - timedelta(minutes=10)
            collect_weather_overview("2026-09-12", "아침", data_dir=self.root,
                                     generator=self.feed)
        fresh = load_weather_overview("2026-09-12", "09:00", "아침",
                                      data_dir=self.root, now=now)
        self.assertEqual(fresh["status"], "ok")
        self.assertTrue(fresh["is_fresh"])
        self.assertEqual(fresh["age_minutes"], 10)

        stale = load_weather_overview("2026-09-12", "09:00", "아침",
                                      data_dir=self.root, now=now + timedelta(seconds=1))
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["is_fresh"])
        self.assertEqual(len(stale["districts"]), 25)

    def test_active_collector_reuses_current_seoul_band(self):
        current = datetime(2026, 9, 17, 17, 5, tzinfo=SEOUL_TZ)
        result = collect_active_weather_overview(
            data_dir=self.root, now=current, generator=self.feed
        )
        self.assertEqual(result["source_date"], "2026-09-17")
        self.assertEqual(result["time_band"], "오후")
        self.assertEqual(result["representative_time"], "18:00")


if __name__ == "__main__":
    unittest.main()
