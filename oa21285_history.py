"""Append-only OA-21285 district snapshots and official v1 time-band summaries."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from common import SEOUL_TZ
    from oa21285_config import OA21285_TIME_BANDS
    from oa21285_percentile import compute_district_population_percentiles
except ImportError:  # pragma: no cover
    from .common import SEOUL_TZ
    from .oa21285_config import OA21285_TIME_BANDS
    from .oa21285_percentile import compute_district_population_percentiles


DEFAULT_HISTORY_DIR = Path(__file__).resolve().parent / "data" / "oa21285" / "history"


def _population_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    for parser in (datetime.fromisoformat, lambda item: datetime.strptime(item, "%Y%m%d%H%M")):
        try:
            return parser(text)
        except (TypeError, ValueError):
            continue
    return None


def save_history_snapshot(
    place_results: Iterable[dict[str, Any]], mappings: Iterable[dict[str, Any]], aggregated: dict[str, Any], *,
    history_dir: str | Path | None = None, received_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[Path | None, bool]:
    """Save once per PPLTN_TIME. Returns (path, created)."""
    places = list(place_results)
    times = sorted({str(item.get("population_time") or "") for item in places if item.get("population_time")})
    if not times:
        return None, False
    population_time = times[-1]
    parsed = _population_datetime(population_time)
    if parsed is None:
        return None, False
    path = (Path(history_dir) if history_dir else DEFAULT_HISTORY_DIR) / parsed.strftime("%Y-%m-%d") / f"{parsed:%H%M}.json"
    if path.exists():
        return path, False
    payload = {
        "received_at": (received_at or datetime.now(SEOUL_TZ)).isoformat(timespec="seconds"),
        "population_time": population_time,
        "poi_current": [{
            "area_cd": item.get("area_cd"), "area_nm": item.get("area_nm"), "source_status": item.get("source_status"),
            "population": item.get("population") or (item.get("current") or {}).get("population"),
            "congestion": item.get("congestion") or (item.get("current") or {}).get("congestion"),
            "replace_yn": item.get("replace_yn") or (item.get("current") or {}).get("replace_yn"),
            "population_time": item.get("population_time"),
        } for item in places],
        "district_mapping": [{
            "area_cd": item.get("area_cd"), "area_nm": item.get("area_nm"), "category": item.get("category"),
            "assigned_district": item.get("assigned_district"), "assignment_status": item.get("assignment_status"),
            "effective_candidates": item.get("effective_candidates", []),
            "ignored_sliver_candidates": item.get("ignored_sliver_candidates", []),
            "out_of_service_area": item.get("out_of_service_area", False),
        } for item in mappings],
        "districts": aggregated.get("districts", {}),
        "pilot": metadata or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path, True


def time_band_for_hour(hour: int) -> str | None:
    return next((name for name, (start, end) in OA21285_TIME_BANDS.items() if start <= hour < end), None)


def summarize_district_history(
    district: str, date: str, time_band: str, *, history_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = (Path(history_dir) if history_dir else DEFAULT_HISTORY_DIR) / date
    values, max_bounds = [], []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parsed = _population_datetime(snapshot.get("population_time"))
        if parsed is None or time_band_for_hour(parsed.hour) != time_band:
            continue
        block = (snapshot.get("districts") or {}).get(district) or {}
        value = block.get("district_population")
        maximum = block.get("district_max_population_reference")
        if isinstance(value, (int, float)):
            values.append(float(value))
            if isinstance(maximum, (int, float)):
                max_bounds.append(float(maximum))
    avg = sum(values) / len(values) if values else None
    max_bound_avg = sum(max_bounds) / len(max_bounds) if max_bounds else None
    return {
        "district": district, "date": date, "time_band": time_band, "valid_snapshot_count": len(values),
        "visitor_avg_in_band": round(avg, 2) if avg is not None else None,
        "visitor_max_in_band": round(max(values), 2) if values else None,
        "visitor_min_in_band": round(min(values), 2) if values else None,
        "visitor_spike_ratio": round(max(values) / avg, 2) if values and avg and avg > 0 else None,
        "max_bound_avg_in_band": round(max_bound_avg, 2) if max_bound_avg is not None else None,
        "max_bound_max_in_band": round(max(max_bounds), 2) if max_bounds else None,
        "max_bound_spike_ratio": round(max(max_bounds) / max_bound_avg, 2) if max_bounds and max_bound_avg and max_bound_avg > 0 else None,
        "source_status": "ok" if values else "no_data",
    }


def summarize_district_percentile_history(
    district: str, date: str, time_band: str, *, history_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate raw population change and same-snapshot percentile history."""
    root = (Path(history_dir) if history_dir else DEFAULT_HISTORY_DIR) / date
    populations, percentiles, timestamps, received_times = [], [], [], []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parsed = _population_datetime(snapshot.get("population_time"))
        if parsed is None or time_band_for_hour(parsed.hour) != time_band:
            continue
        ranked = compute_district_population_percentiles(snapshot.get("districts") or {})
        district_rank = ranked.get(district) or {}
        population = district_rank.get("population")
        percentile = district_rank.get("population_percentile")
        if not isinstance(population, (int, float)) or not isinstance(percentile, (int, float)):
            continue
        populations.append(float(population))
        percentiles.append(float(percentile))
        timestamps.append(str(snapshot.get("population_time") or ""))
        received_times.append(str(snapshot.get("received_at") or ""))
    population_avg = sum(populations) / len(populations) if populations else None
    return {
        "district": district, "date": date, "time_band": time_band,
        "snapshot_count": len(populations), "source_timestamps": timestamps,
        "first_population_time": timestamps[0] if timestamps else None,
        "last_population_time": timestamps[-1] if timestamps else None,
        "latest_population_time": timestamps[-1] if timestamps else None,
        "latest_received_at": received_times[-1] if received_times else None,
        "population_raw_current": round(populations[-1], 2) if populations else None,
        "population_raw_avg_in_band": round(population_avg, 2) if population_avg is not None else None,
        "population_raw_max_in_band": round(max(populations), 2) if populations else None,
        "population_raw_min_in_band": round(min(populations), 2) if populations else None,
        "population_percentile_avg_in_band": round(sum(percentiles) / len(percentiles), 2) if percentiles else None,
        "population_percentile_max_in_band": round(max(percentiles), 2) if percentiles else None,
        "population_percentile_min_in_band": round(min(percentiles), 2) if percentiles else None,
        "population_spike_ratio": round(max(populations) / population_avg, 4) if populations and population_avg and population_avg > 0 else None,
        "source_status": "ok" if populations else "no_data",
        "normalization": "seoul_district_percentile",
    }
