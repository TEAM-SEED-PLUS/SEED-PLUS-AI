"""Request-safe KOPIS snapshot filtering."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from common import (build_query_context, closest_time_match, extract_times_from_text,
                    normalize_text, time_matches_band)
from snapshot_cache import read_snapshot, snapshot_age_minutes

DEFAULT_PERFORMANCE_DIR = Path(__file__).resolve().parent / "data" / "performance" / "latest"
PERFORMANCE_CACHE_TTL_MINUTES = 60


def performance_snapshot_path(date_str: str, data_dir: str | Path | None = None) -> Path:
    return (Path(data_dir) if data_dir else DEFAULT_PERFORMANCE_DIR) / f"{date_str}.json"


def get_cached_performances(district: str, date_str: str | None = None, time_str: str | None = None,
                            kopis_key: str = "", limit: int = 10, *,
                            data_dir: str | Path | None = None, now: datetime | None = None,
                            ttl_minutes: int = PERFORMANCE_CACHE_TTL_MINUTES) -> dict[str, Any]:
    # kopis_key remains in the signature for drop-in compatibility; loaders never use it.
    del kopis_key
    ctx = build_query_context(district, date_str, time_str)
    base = {"district": ctx.district_ko, "date": ctx.date_str, "time": ctx.time_str,
            "time_band": ctx.time_band, "count": 0, "items": []}
    snapshot = read_snapshot(performance_snapshot_path(ctx.date_str, data_dir))
    if snapshot is None:
        base["source_status"] = {"status": "no_data", "source": "performance", "eligibility_reason": "cache_missing"}
        return base
    age = snapshot_age_minutes(snapshot, now)
    metadata = {"source": "performance", "age_minutes": age, "source_time": snapshot.get("generated_at")}
    if age is None or age > ttl_minutes:
        base["source_status"] = {"status": "no_data", **metadata, "eligibility_reason": "stale_cache"}
        return base
    if snapshot.get("source_status") == "failed":
        base["source_status"] = {"status": "failed", **metadata, "eligibility_reason": "collector_failed"}
        return base
    items = []
    for raw in snapshot.get("items") or []:
        row_district = normalize_text(raw.get("district_from_address") or raw.get("collection_district"))
        if row_district != ctx.district_ko:
            continue
        guidance = normalize_text(raw.get("schedule_text") or raw.get("time_text"))
        candidates = extract_times_from_text(guidance)
        if not time_matches_band(candidates, ctx.time_band):
            continue
        chosen = closest_time_match(candidates, ctx.target_datetime.time())
        item = {key: raw.get(key) for key in (
            "source", "mt20id", "title", "genre", "place", "address", "district_from_address",
            "date_from", "date_to", "runtime", "price_text", "openrun", "poster")}
        item["source"] = item.get("source") or "KOPIS"
        item["time_text"] = chosen.strftime("%H:%M") if chosen else guidance
        items.append(item)
    items.sort(key=lambda x: (x["time_text"] == "", x["time_text"], x["title"] or ""))
    result_status = "empty"
    if items:
        result_status = "partial" if snapshot.get("source_status") == "partial" else "ok"
    base.update({"count": len(items), "items": items[:limit],
                 "source_status": {"status": result_status, **metadata}})
    return base
