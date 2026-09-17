import asyncio
from datetime import date
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import lifespan
from weather_overview_scheduler import (
    WeatherOverviewScheduler,
    WeatherOverviewSchedulerSettings,
    cleanup_weather_overview_snapshots,
)


class SchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_disabled_does_not_call_collector(self):
        settings = WeatherOverviewSchedulerSettings(enabled=False)
        with patch("app.main.WeatherOverviewSchedulerSettings.from_env", return_value=settings), \
             patch("weather_overview_collector.collect_active_weather_overview") as collector:
            async with lifespan(None):
                await asyncio.sleep(0)
        collector.assert_not_called()

    async def test_scheduler_enabled_calls_collector_immediately(self):
        settings = WeatherOverviewSchedulerSettings(enabled=True, interval_minutes=5)
        called = threading.Event()

        def collect(**_kwargs):
            called.set()
            return {"source_status": "ok", "district_count": 25}

        with tempfile.TemporaryDirectory() as temporary:
            settings = WeatherOverviewSchedulerSettings(
                enabled=True, interval_minutes=5, data_dir=Path(temporary) / "latest"
            )
            with patch("app.main.WeatherOverviewSchedulerSettings.from_env", return_value=settings), \
                 patch("weather_overview_collector.collect_active_weather_overview", side_effect=collect):
                async with lifespan(None):
                    self.assertTrue(await asyncio.to_thread(called.wait, 2))

    async def test_overlapping_tick_is_skipped_in_process(self):
        started = threading.Event()
        release = threading.Event()

        def blocked(_settings):
            started.set()
            release.wait(2)
            return True

        scheduler = WeatherOverviewScheduler(WeatherOverviewSchedulerSettings())
        with patch("weather_overview_scheduler._run_locked_collector", side_effect=blocked) as run:
            first = asyncio.create_task(scheduler.run_once())
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            self.assertFalse(await scheduler.run_once())
            release.set()
            self.assertTrue(await first)
        self.assertEqual(run.call_count, 1)

    async def test_collector_exception_does_not_stop_scheduler(self):
        sleeps = 0

        async def stop_after_second_tick(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                raise asyncio.CancelledError

        scheduler = WeatherOverviewScheduler(
            WeatherOverviewSchedulerSettings(), sleep=stop_after_second_tick
        )
        with patch("weather_overview_scheduler._run_locked_collector",
                   side_effect=[RuntimeError("upstream failed"), True]) as run:
            with self.assertRaises(asyncio.CancelledError):
                await scheduler.run()
        self.assertEqual(run.call_count, 2)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, name: str) -> Path:
        path = self.root / name
        path.write_text("{}", encoding="utf-8")
        return path

    def cleanup(self):
        return cleanup_weather_overview_snapshots(
            self.root, 7, today=date(2026, 9, 17)
        )

    def test_outside_retention_is_deleted_and_inside_is_preserved(self):
        expired = self.create("2026-09-10_아침.json")
        boundary = self.create("2026-09-11_아침.json")
        self.assertEqual(self.cleanup(), [expired])
        self.assertFalse(expired.exists())
        self.assertTrue(boundary.exists())

    def test_today_snapshot_is_preserved(self):
        today = self.create("2026-09-17_저녁.json")
        self.cleanup()
        self.assertTrue(today.exists())

    def test_nonmatching_files_are_preserved(self):
        files = [
            self.create("notes.json"),
            self.create("2026-09-10_새벽.json"),
            self.create("2026-09-10_아침.json.tmp"),
            self.create(".collector.lock"),
        ]
        self.cleanup()
        self.assertTrue(all(path.exists() for path in files))

    def test_invalid_date_is_preserved(self):
        invalid = self.create("2026-02-30_아침.json")
        self.cleanup()
        self.assertTrue(invalid.exists())

    def test_multiple_time_bands_are_deleted(self):
        files = [self.create(f"2026-09-01_{band}.json")
                 for band in ("심야", "아침", "점심", "오후", "저녁")]
        deleted = self.cleanup()
        self.assertEqual(set(deleted), set(files))
        self.assertTrue(all(not path.exists() for path in files))


class SchedulerSettingsTests(unittest.TestCase):
    def test_retention_days_environment_parsing(self):
        with patch.dict("os.environ", {"WEATHER_OVERVIEW_RETENTION_DAYS": "14"}, clear=True):
            self.assertEqual(WeatherOverviewSchedulerSettings.from_env().retention_days, 14)
        with patch.dict("os.environ", {"WEATHER_OVERVIEW_RETENTION_DAYS": "0"}, clear=True):
            self.assertEqual(WeatherOverviewSchedulerSettings.from_env().retention_days, 7)

    def test_interval_environment_parsing(self):
        with patch.dict("os.environ", {"WEATHER_OVERVIEW_INTERVAL_MINUTES": "2.5"}, clear=True):
            self.assertEqual(WeatherOverviewSchedulerSettings.from_env().interval_minutes, 2.5)
        with patch.dict("os.environ", {"WEATHER_OVERVIEW_INTERVAL_MINUTES": "invalid"}, clear=True):
            self.assertEqual(WeatherOverviewSchedulerSettings.from_env().interval_minutes, 5)


if __name__ == "__main__":
    unittest.main()
