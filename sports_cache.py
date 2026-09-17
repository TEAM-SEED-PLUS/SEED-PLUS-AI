"""Request-safe sports snapshot loader; this module never starts Playwright."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from common import build_query_context, classify_time_band, normalize_text
from snapshot_cache import read_snapshot, snapshot_age_minutes

# Keep the FastAPI import graph free of pandas/BeautifulSoup/Playwright. The
# collector uses sports_api's canonical implementation; this tiny fallback is
# only for backward-compatible snapshots that predate the district field.
def infer_district_from_stadium(stadium: str) -> str:
    from common import SPORTS_STADIUM_DISTRICT_HINTS
    value = normalize_text(stadium).lower()
    for keyword, district in SPORTS_STADIUM_DISTRICT_HINTS.items():
        if keyword.lower() in value:
            return district
    return ""

DEFAULT_SPORTS_DIR = Path(__file__).resolve().parent / "data" / "sports" / "latest"
SPORTS_CACHE_TTL_MINUTES = 30


def sports_snapshot_path(date_str: str, data_dir: str | Path | None = None) -> Path:
    return (Path(data_dir) if data_dir else DEFAULT_SPORTS_DIR) / f"{date_str}.json"


def get_cached_sports(district: str, date_str: str | None = None, time_str: str | None = None,
                      limit: int = 10, *, data_dir: str | Path | None = None,
                      now: datetime | None = None, ttl_minutes: int = SPORTS_CACHE_TTL_MINUTES) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    base = {"district": ctx.district_ko, "date": ctx.date_str, "time": ctx.time_str,
            "time_band": ctx.time_band, "count": 0, "items": []}
    snapshot = read_snapshot(sports_snapshot_path(ctx.date_str, data_dir))
    if snapshot is None:
        base["source_status"] = {"status": "no_data", "source": "sports", "eligibility_reason": "cache_missing"}
        return base
    age = snapshot_age_minutes(snapshot, now)
    metadata = {"source": "sports", "age_minutes": age, "source_time": snapshot.get("generated_at")}
    if age is None or age > ttl_minutes:
        base["source_status"] = {"status": "no_data", **metadata, "eligibility_reason": "stale_cache"}
        return base
    if snapshot.get("source_status") == "failed":
        base["source_status"] = {"status": "failed", **metadata, "eligibility_reason": "collector_failed"}
        return base
    items = []
    for raw in snapshot.get("items") or []:
        stadium = normalize_text(raw.get("stadium"))
        row_district = normalize_text(raw.get("district")) or infer_district_from_stadium(stadium)
        if row_district != ctx.district_ko:
            continue
        try:
            band = classify_time_band(build_query_context(ctx.district_ko, ctx.date_str, raw.get("time")).target_datetime.time())
        except Exception:
            band = ctx.time_band
        if band != ctx.time_band:
            continue
        items.append({"source": raw.get("league"), "sport": raw.get("sport"), "league": raw.get("league"),
                      "date": raw.get("date"), "time": raw.get("time"), "match": raw.get("match"),
                      "stadium": stadium, "district": row_district, "link_url": raw.get("link_url")})
    items.sort(key=lambda x: (x.get("time") or "", x.get("league") or "", x.get("match") or ""))
    base.update({"count": len(items), "items": items[:limit],
                 "source_status": {"status": "ok" if items else "empty", **metadata}})
    return base
