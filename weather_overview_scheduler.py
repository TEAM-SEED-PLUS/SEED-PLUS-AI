"""Application-level scheduler and retention for weather overview snapshots."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import re
import time
from typing import Any, Iterator

try:  # Linux/macOS production lock; the asyncio lock remains as a fallback.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX development only
    fcntl = None  # type: ignore[assignment]

from common import SEOUL_TZ
import weather_overview_collector
from weather_overview_cache import DEFAULT_OVERVIEW_DIR
from snapshot_cache import read_snapshot, write_snapshot
from operations_settings import (WEATHER_OVERVIEW_ENABLED, WEATHER_OVERVIEW_INTERVAL_MINUTES,
                                 WEATHER_OVERVIEW_RETENTION_DAYS)

LOGGER = logging.getLogger(__name__)
SNAPSHOT_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(심야|아침|점심|오후|저녁)\.json$"
)


@dataclass(frozen=True)
class WeatherOverviewSchedulerSettings:
    enabled: bool = WEATHER_OVERVIEW_ENABLED
    interval_minutes: float = WEATHER_OVERVIEW_INTERVAL_MINUTES
    retention_days: int = WEATHER_OVERVIEW_RETENTION_DAYS
    data_dir: Path = DEFAULT_OVERVIEW_DIR

    @classmethod
    def from_env(cls) -> "WeatherOverviewSchedulerSettings":
        return cls()


@contextmanager
def _collector_file_lock(lock_path: Path) -> Iterator[bool]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        if fcntl is None:
            raise RuntimeError("File locking is unavailable on this platform")
            return
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def cleanup_weather_overview_snapshots(data_dir: str | Path, retention_days: int, *,
                                       today: date | None = None) -> list[Path]:
    """Delete matching snapshots older than the inclusive retention window."""
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    root = Path(data_dir)
    if not root.exists():
        return []
    current = today or datetime.now(SEOUL_TZ).date()
    oldest_kept = current - timedelta(days=retention_days - 1)
    deleted = []
    for path in root.iterdir():
        match = SNAPSHOT_PATTERN.fullmatch(path.name)
        if match is None or not path.is_file():
            continue
        try:
            snapshot_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if snapshot_date < oldest_kept:
            path.unlink()
            deleted.append(path)
    return deleted


def _run_locked_collector(settings: WeatherOverviewSchedulerSettings) -> bool:
    lock_path = settings.data_dir.parent / ".collector.lock"
    with _collector_file_lock(lock_path) as acquired:
        if not acquired:
            LOGGER.info("Weather overview collector already running; skip")
            return False
        state_path = settings.data_dir.parent / ".collector-state.json"
        now = datetime.now(SEOUL_TZ)
        previous = (read_snapshot(state_path) or {}).get("attempted_at")
        if previous and now - datetime.fromisoformat(previous) < timedelta(minutes=settings.interval_minutes):
            return False
        write_snapshot(state_path, {"attempted_at": now.isoformat()})
        started = time.monotonic()
        LOGGER.info("Weather overview collector run started")
        result = weather_overview_collector.collect_active_weather_overview(
            data_dir=settings.data_dir
        )
        LOGGER.info(
            "Weather overview collector completed source_status=%s district_count=%s duration_seconds=%.2f",
            result.get("source_status"), result.get("district_count"), time.monotonic() - started,
        )
        try:
            deleted = cleanup_weather_overview_snapshots(
                settings.data_dir, settings.retention_days
            )
            deleted_dates = sorted({path.name[:10] for path in deleted})
            LOGGER.info("Weather overview retention deleted_count=%s deleted_dates=%s",
                        len(deleted), deleted_dates)
        except Exception as exc:
            LOGGER.warning("Weather overview retention cleanup failed error_type=%s",
                           type(exc).__name__)
        return True


class WeatherOverviewScheduler:
    def __init__(self, settings: WeatherOverviewSchedulerSettings, *,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self.settings = settings
        self._sleep = sleep
        self._process_lock = asyncio.Lock()

    async def run_once(self) -> bool:
        if self._process_lock.locked():
            LOGGER.info("Weather overview collector already running in process; skip")
            return False
        async with self._process_lock:
            worker = asyncio.create_task(
                asyncio.to_thread(_run_locked_collector, self.settings)
            )
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A blocking collector cannot be force-cancelled safely. Keep the
                # file/process locks until its thread has completed, then finish
                # application shutdown.
                LOGGER.info("Weather overview scheduler shutdown waiting for active collector")
                await worker
                raise
            except Exception as exc:
                # Upstream exception messages can contain request details. Log
                # only the exception class so credentials cannot be exposed.
                LOGGER.error("Weather overview collector failed error_type=%s",
                             type(exc).__name__)
                return False

    async def run(self) -> None:
        LOGGER.info("Weather overview scheduler started interval_minutes=%s retention_days=%s",
                    self.settings.interval_minutes, self.settings.retention_days)
        while True:
            await self.run_once()
            await self._sleep(self.settings.interval_minutes * 60)
