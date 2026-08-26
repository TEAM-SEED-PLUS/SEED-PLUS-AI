"""Manual OA-21285 history pilot: probe first, collect only on a new PPLTN_TIME."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from common import SEOUL_TZ, TOURISM_DATA_SEOUL_SIGNGU
    from oa21285_aggregation import _assigned_district, aggregate_district_populations
    from oa21285_api import OA21285Client
    from oa21285_cache import save_cached_place
    from oa21285_config import OA21285_CATEGORY_WEIGHTS
    from oa21285_history import DEFAULT_HISTORY_DIR, save_history_snapshot
except ImportError:  # pragma: no cover
    from .common import SEOUL_TZ, TOURISM_DATA_SEOUL_SIGNGU
    from .oa21285_aggregation import _assigned_district, aggregate_district_populations
    from .oa21285_api import OA21285Client
    from .oa21285_cache import save_cached_place
    from .oa21285_config import OA21285_CATEGORY_WEIGHTS
    from .oa21285_history import DEFAULT_HISTORY_DIR, save_history_snapshot


ROOT = Path(__file__).resolve().parent
DEFAULT_PLACES_PATH = ROOT / "data" / "oa21285" / "reference" / "places_20260402.json"
DEFAULT_MAPPINGS_PATH = ROOT / "data" / "oa21285" / "reference" / "mapping_candidates_20260402.json"
DEFAULT_PROBE_POI = "POI014"


def load_official_pilot_targets(places_path: str | Path = DEFAULT_PLACES_PATH, mappings_path: str | Path = DEFAULT_MAPPINGS_PATH) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    places_doc = json.loads(Path(places_path).read_text(encoding="utf-8"))
    mappings_doc = json.loads(Path(mappings_path).read_text(encoding="utf-8"))
    places_by_code = {str(item["area_cd"]): item for item in places_doc["places"]}
    places, mappings = [], []
    for source in mappings_doc["mappings"]:
        district, status = _assigned_district(source)
        if source.get("category") not in OA21285_CATEGORY_WEIGHTS or not district:
            continue
        mapping = dict(source)
        mapping.update({"assigned_district": district, "assignment_status": status})
        mappings.append(mapping)
        places.append(places_by_code[str(source["area_cd"])])
    return places, mappings


def list_history_snapshots(history_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(history_dir) if history_dir else DEFAULT_HISTORY_DIR
    snapshots = []
    for path in sorted(root.glob("*/*.json")) if root.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        snapshots.append({"path": str(path), "population_time": payload.get("population_time"),
                          "received_at": payload.get("received_at"),
                          "api_call_count": (payload.get("pilot") or {}).get("api_call_count")})
    return snapshots


def _coverage(mappings: list[dict[str, Any]], results: list[dict[str, Any]], aggregated: dict[str, Any]) -> dict[str, Any]:
    statuses = {str(item.get("area_cd") or ""): str(item.get("source_status") or "no_data") for item in results}
    assigned: dict[str, list[str]] = {}
    for item in mappings:
        assigned.setdefault(str(item["assigned_district"]), []).append(str(item["area_cd"]))
    output = {}
    for district in TOURISM_DATA_SEOUL_SIGNGU:
        codes = assigned.get(district, [])
        counts = Counter(statuses.get(code, "no_data") for code in codes)
        block = (aggregated.get("districts") or {}).get(district) or {}
        output[district] = {
            "assigned_poi_count": len(codes), "ok_poi_count": counts["ok"],
            "no_data_poi_count": counts["no_data"], "failed_poi_count": counts["failed"],
            "coverage_ratio": round(counts["ok"] / len(codes), 6) if codes else None,
            "district_population": block.get("district_population"),
            "source_status": block.get("source_status", "no_data"),
        }
    return output


def run_pilot_once(*, client: OA21285Client | None = None, probe_poi: str = DEFAULT_PROBE_POI,
                   history_dir: str | Path | None = None, cache_dir: str | Path | None = None,
                   places_path: str | Path = DEFAULT_PLACES_PATH,
                   mappings_path: str | Path = DEFAULT_MAPPINGS_PATH,
                   received_at: datetime | None = None) -> dict[str, Any]:
    api = client or OA21285Client()
    places, mappings = load_official_pilot_targets(places_path, mappings_path)
    places_by_code = {str(item["area_cd"]): item for item in places}
    probe_code = probe_poi if probe_poi in places_by_code else str(places[0]["area_cd"])
    probe = api.get_place(probe_code, reference_place=places_by_code[probe_code])
    probe_time = str(probe.get("population_time") or "")
    snapshots = list_history_snapshots(history_dir)
    latest_time = str(snapshots[-1].get("population_time") or "") if snapshots else ""
    if probe.get("source_status") != "ok" or not probe_time:
        return {"status": "probe_failed", "probe_poi": probe_code, "probe": probe,
                "latest_population_time": latest_time, "api_call_count": 1}
    if probe_time == latest_time or any(str(item.get("population_time") or "") == probe_time for item in snapshots):
        return {"status": "unchanged", "probe_poi": probe_code, "population_time": probe_time,
                "latest_population_time": latest_time, "api_call_count": 1, "batch_skipped": True}

    results = [probe]
    save_cached_place(probe, cache_dir=cache_dir, received_at=received_at)
    for place in places:
        code = str(place["area_cd"])
        if code == probe_code:
            continue
        result = api.get_place(code, reference_place=place)
        results.append(result)
        if result.get("area_cd"):
            save_cached_place(result, cache_dir=cache_dir, received_at=received_at)
    aggregated = aggregate_district_populations(mappings, results)
    coverage = _coverage(mappings, results, aggregated)
    metadata = {"probe_poi": probe_code, "api_call_count": len(results), "target_poi_count": len(places),
                "poi_coverage": coverage}
    path, created = save_history_snapshot(results, mappings, aggregated, history_dir=history_dir,
                                          received_at=received_at or datetime.now(SEOUL_TZ), metadata=metadata)
    return {"status": "collected" if created else "unchanged", "probe_poi": probe_code,
            "population_time": probe_time, "api_call_count": len(results), "target_poi_count": len(places),
            "history_created": created, "history_path": str(path) if path else None,
            "poi_coverage": coverage, "districts": aggregated["districts"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="OA-21285 manual history pilot collector")
    parser.add_argument("--probe-poi", default=DEFAULT_PROBE_POI)
    parser.add_argument("--history-dir", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--list", action="store_true", help="API call 없이 누적 snapshot 목록만 출력")
    parser.add_argument("--full", action="store_true", help="수집 시 POI contribution까지 전체 출력")
    args = parser.parse_args()
    result = {"snapshots": list_history_snapshots(args.history_dir)} if args.list else run_pilot_once(
        probe_poi=args.probe_poi, history_dir=args.history_dir, cache_dir=args.cache_dir)
    if not args.full:
        result.pop("districts", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
