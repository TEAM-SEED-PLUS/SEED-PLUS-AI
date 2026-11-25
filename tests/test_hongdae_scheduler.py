import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from common import SEOUL_TZ
from hongdae_scheduler import due_slots, run_due, week_start
from snapshot_cache import read_snapshot, write_snapshot
from weather_overview_scheduler import _collector_file_lock


THURSDAY = datetime(2026, 11, 26, 6, 30, tzinfo=timezone.utc)


def test_seoul_slots_and_prior_monday():
    daily, weekly = due_slots(THURSDAY)
    assert daily.isoformat() == weekly.isoformat() == "2026-11-26"
    assert week_start(weekly).isoformat() == "2026-11-16"
    assert due_slots(THURSDAY - timedelta(minutes=1))[1].isoformat() == "2026-11-19"


def test_scheduler_recovers_and_republishes_partial(tmp_path):
    root = tmp_path / "volume"
    state = root / "scheduler_state.json"
    output = root / "weekly/2026-11-16_2026-11-22.json"
    collect = Mock(side_effect=[
        {"statuses": {"2026-11-22": "missing"}},
        {"statuses": {"2026-11-22": "stored"}},
    ])
    ready = root / "backfill/2026-11-22_심야.json"

    def calculate(days):
        if days == ["2026-11-22"]:
            write_snapshot(ready, {"observation": True})

    def publish(start, end, **kwargs):
        write_snapshot(kwargs["output_dir"] / f"{start}_{end}.json",
                       {"completeness": "complete" if ready.exists() else "partial"})

    with patch("hongdae_scheduler.collect", collect), patch("hongdae_scheduler.calculate_days", side_effect=calculate), \
         patch("hongdae_scheduler.build_weekly_briefing", side_effect=publish) as build, \
         patch("hongdae_scheduler.MANIFEST", root / "population_collection.json"):
        run_due(THURSDAY + timedelta(hours=1), root=root)
        assert read_snapshot(output)["completeness"] == "partial"
        write_snapshot(root / "population_collection.json", {"retry_dates": ["2026-11-22"]})
        run_due(THURSDAY + timedelta(hours=2), root=root)
        assert collect.call_count == 1
        run_due(THURSDAY + timedelta(days=1), root=root)
        assert "2026-11-22" in collect.call_args.args[0]
        assert read_snapshot(output)["completeness"] == "complete"
        assert read_snapshot(state)["last_analysis_period"]["end"] == "2026-11-22"
        assert build.call_count == 2


def test_manual_lock_excludes_scheduler_and_failed_write_preserves_snapshot(tmp_path):
    with _collector_file_lock(tmp_path / ".population.lock") as locked, patch("hongdae_scheduler.collect") as collect:
        assert locked
        run_due(THURSDAY, root=tmp_path)
        collect.assert_not_called()
    path = tmp_path / "weekly.json"
    write_snapshot(path, {"valid": True})
    with pytest.raises(TypeError):
        write_snapshot(path, {"bad": object()})
    assert read_snapshot(path) == {"valid": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_fastapi_lifecycle_and_responsive_api():
    from app.main import app
    from weather_overview_scheduler import WeatherOverviewSchedulerSettings

    async def scheduled():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    from threading import Event
    started, stopped = Event(), Event()
    with patch("app.main.hongdae_scheduler.run", side_effect=scheduled), \
         patch("app.main.WeatherOverviewSchedulerSettings.from_env",
               return_value=WeatherOverviewSchedulerSettings(enabled=False)):
        with TestClient(app) as client:
            assert started.wait(2)
            assert client.get("/health").status_code == 200
        assert stopped.is_set()
