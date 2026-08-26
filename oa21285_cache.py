"""OA-21285 5분 데이터의 API-key-free latest cache와 batch collector."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from common import SEOUL_TZ
    from oa21285_api import OA21285Client, collected_record
    from oa21285_aggregation import aggregate_district_populations
    from oa21285_history import save_history_snapshot
except ImportError:  # pragma: no cover
    from .common import SEOUL_TZ
    from .oa21285_api import OA21285Client, collected_record
    from .oa21285_aggregation import aggregate_district_populations
    from .oa21285_history import save_history_snapshot


DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "data" / "oa21285" / "cache" / "latest"
DEFAULT_CACHE_TTL_MINUTES = 5


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=SEOUL_TZ) if parsed.tzinfo is None else parsed


def cache_path(area_cd: str, cache_dir: str | Path | None = None) -> Path:
    code = str(area_cd).strip().upper()
    if not code.startswith("POI") or not code[3:].isdigit():
        raise ValueError("cache key는 POI 형식의 AREA_CD여야 합니다.")
    return (Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR) / f"{code}.json"


def save_cached_place(result: dict[str, Any], *, cache_dir: str | Path | None = None, received_at: datetime | None = None) -> Path:
    path = cache_path(str(result.get("area_cd") or ""), cache_dir)
    payload = collected_record(result, received_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_cached_place(
    area_cd: str, *, cache_dir: str | Path | None = None, now: datetime | None = None,
    ttl_minutes: int = DEFAULT_CACHE_TTL_MINUTES,
) -> dict[str, Any] | None:
    path = cache_path(area_cd, cache_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    received_at = _parse_datetime(payload.get("received_at"))
    current = now or datetime.now(SEOUL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL_TZ)
    age_minutes = (current - received_at).total_seconds() / 60 if received_at else None
    population_time = _parse_datetime(payload.get("population_time"))
    population_age = (current - population_time).total_seconds() / 60 if population_time else None
    payload["age_minutes"] = round(age_minutes, 2) if age_minutes is not None else None
    payload["received_age_minutes"] = payload["age_minutes"]
    payload["population_age_minutes"] = round(population_age, 2) if population_age is not None else None
    payload["cache_fresh"] = bool(age_minutes is not None and 0 <= age_minutes <= ttl_minutes)
    return payload


def collect_oa21285_places(
    places: Iterable[dict[str, Any]], *, client: OA21285Client | None = None,
    cache_dir: str | Path | None = None, now: datetime | None = None,
    ttl_minutes: int = DEFAULT_CACHE_TTL_MINUTES, use_fresh_cache: bool = True,
    mappings: Iterable[dict[str, Any]] | None = None, history_dir: str | Path | None = None,
) -> dict[str, Any]:
    api = client or OA21285Client()
    results = []
    seen: set[str] = set()
    cache_hits = 0
    api_calls = 0
    for place in places:
        area_cd = str(place.get("area_cd") or place.get("AREA_CD") or "").strip().upper()
        if not area_cd or area_cd in seen:
            continue
        seen.add(area_cd)
        cached = load_cached_place(area_cd, cache_dir=cache_dir, now=now, ttl_minutes=ttl_minutes)
        if use_fresh_cache and cached and cached.get("cache_fresh"):
            results.append(cached)
            cache_hits += 1
            continue
        result = api.get_place(area_cd, reference_place=place)
        api_calls += 1
        if result.get("area_cd"):
            save_cached_place(result, cache_dir=cache_dir, received_at=now)
        results.append(result)
    output = {"place_count": len(seen), "api_call_count": api_calls, "cache_hit_count": cache_hits, "places": results}
    if mappings is not None:
        mapping_rows = list(mappings)
        aggregated = aggregate_district_populations(mapping_rows, results)
        history_path, created = save_history_snapshot(
            results, mapping_rows, aggregated, history_dir=history_dir, received_at=now,
        )
        output.update({"aggregation": aggregated, "history_path": str(history_path) if history_path else None,
                       "history_created": created})
    return output
