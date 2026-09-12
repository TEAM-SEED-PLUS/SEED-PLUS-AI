"""Read-only request access to offline Seoul weather-overview snapshots."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from common import SEOUL_TZ, TIME_BAND_REPRESENTATIVE_TIMES
from snapshot_cache import read_snapshot, snapshot_age_minutes

DEFAULT_OVERVIEW_DIR = Path(__file__).resolve().parent / "data" / "weather_overview" / "latest"
OVERVIEW_FRESH_TTL_MINUTES = 10


def overview_snapshot_path(date_str: str, time_band: str,
                           data_dir: str | Path | None = None) -> Path:
    return (Path(data_dir) if data_dir else DEFAULT_OVERVIEW_DIR) / f"{date_str}_{time_band}.json"


def load_weather_overview(date_str: str, time_str: str, time_band: str, *,
                          data_dir: str | Path | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
    snapshot = read_snapshot(overview_snapshot_path(date_str, time_band, data_dir))
    current = now or datetime.now(SEOUL_TZ)
    if snapshot is None:
        return {"schema_version": "1.0", "query": {"date": date_str,
                "time": TIME_BAND_REPRESENTATIVE_TIMES[time_band],
                "time_band": time_band}, "status": "no_data", "districts": [],
                "generated_at": current.isoformat(timespec="seconds"),
                "source_time": None, "age_minutes": None,
                "fresh_ttl_minutes": OVERVIEW_FRESH_TTL_MINUTES,
                "is_fresh": False}
    districts = snapshot.get("districts") if isinstance(snapshot.get("districts"), list) else []
    status = snapshot.get("source_status")
    if status not in {"ok", "partial", "failed", "no_data"}:
        status = "no_data" if not districts else "partial"
    age_minutes = snapshot_age_minutes(snapshot, current)
    is_fresh = age_minutes is not None and age_minutes <= OVERVIEW_FRESH_TTL_MINUTES
    if not is_fresh:
        status = "stale"
    snapshot_time = str(snapshot.get("representative_time") or TIME_BAND_REPRESENTATIVE_TIMES[time_band])
    return {"schema_version": "1.0", "query": {"date": date_str, "time": snapshot_time,
            "time_band": time_band}, "status": status, "districts": districts,
            "generated_at": current.isoformat(timespec="seconds"),
            "source_time": snapshot.get("generated_at"),
            "age_minutes": age_minutes,
            "fresh_ttl_minutes": OVERVIEW_FRESH_TTL_MINUTES,
            "is_fresh": is_fresh}
