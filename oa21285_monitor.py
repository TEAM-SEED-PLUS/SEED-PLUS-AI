"""Read-only production diagnostics for OA-21285 history and provider eligibility."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from common import SEOUL_TZ, TOURISM_DATA_SEOUL_SIGNGU
from oa21285_config import OA21285_MIN_BAND_SNAPSHOTS
from oa21285_history import DEFAULT_HISTORY_DIR, summarize_district_percentile_history, time_band_for_hour
from oa21285_percentile import compute_district_population_percentiles


def build_monitoring_diagnostics(date: str, time_band: str, *, history_dir=None) -> dict:
    root = (Path(history_dir) if history_dir else DEFAULT_HISTORY_DIR) / date
    all_snapshots, snapshots = [], []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            parsed = datetime.fromisoformat(str(payload.get("population_time")))
        except (OSError, ValueError, TypeError):
            continue
        all_snapshots.append(payload)
        if time_band_for_hour(parsed.hour) == time_band:
            snapshots.append(payload)
    latest = snapshots[-1] if snapshots else {}
    latest_districts = latest.get("districts") or {}
    ranked = compute_district_population_percentiles(latest_districts)
    districts, no_data, fallback_count, failed_pois = {}, [], 0, 0
    for district in TOURISM_DATA_SEOUL_SIGNGU:
        summary = summarize_district_percentile_history(district, date, time_band, history_dir=history_dir)
        eligible = summary["snapshot_count"] >= OA21285_MIN_BAND_SNAPSHOTS and summary["source_status"] == "ok" and time_band != "심야"
        block = latest_districts.get(district) or {}
        contributions = block.get("contributions") or []
        total_weight = sum(float(item.get("final_weight") or 0) for item in contributions)
        dominant = max(contributions, key=lambda item: float(item.get("final_weight") or 0), default={})
        dominant_ratio = float(dominant.get("final_weight") or 0) / total_weight if total_weight else None
        if not eligible:
            fallback_count += 1
        if block.get("source_status", "no_data") == "no_data":
            no_data.append(district)
        fallback_reason = None
        if not eligible:
            if time_band == "심야":
                fallback_reason = "night_policy"
            elif block.get("source_status", "no_data") in {"no_data", "failed"}:
                fallback_reason = f"oa_{block.get('source_status', 'no_data')}"
            else:
                fallback_reason = "insufficient_current_date_band_history"
        districts[district] = {
            "provider": {"inflow": "oa21285" if eligible else "sdot", "spending": "oa21285" if eligible else "sdot", "competition": "sdot"},
            "snapshot_count": summary["snapshot_count"],
            "percentile": (ranked.get(district) or {}).get("population_percentile"),
            "source_status": block.get("source_status", "no_data"),
            "fallback": not eligible,
            "fallback_reason": fallback_reason,
            "dominant_poi": dominant.get("area_nm"),
            "dominant_poi_ratio": round(dominant_ratio, 4) if dominant_ratio is not None else None,
            "warning": bool(dominant_ratio is not None and dominant_ratio >= 0.8),
        }
    for item in latest.get("poi_current") or []:
        failed_pois += int(item.get("source_status") == "failed")
    received = latest.get("received_at")
    return {
        "date": date, "time_band": time_band,
        "latest_population_time": latest.get("population_time"),
        "latest_collection_success_at": received,
        "snapshot_count": len(snapshots),
        "snapshot_counts_by_time_band": {
            band: sum(1 for payload in all_snapshots if time_band_for_hour(datetime.fromisoformat(payload["population_time"]).hour) == band)
            for band in ("심야", "아침", "점심", "오후", "저녁")
        },
        "districts": districts, "no_data_districts": no_data,
        "failed_poi_count": failed_pois, "fallback_district_count": fallback_count,
        "percentile_distribution": sorted(value["percentile"] for value in districts.values() if value["percentile"] is not None),
    }


def main() -> None:
    now = datetime.now(SEOUL_TZ)
    parser = argparse.ArgumentParser(description="OA-21285 production monitoring diagnostics")
    parser.add_argument("--date", default=now.strftime("%Y-%m-%d"))
    parser.add_argument("--time-band", default=time_band_for_hour(now.hour))
    parser.add_argument("--history-dir", default=None)
    args = parser.parse_args()
    print(json.dumps(build_monitoring_diagnostics(args.date, args.time_band, history_dir=args.history_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
