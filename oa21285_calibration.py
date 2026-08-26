"""Diagnostics-only OA population calibration candidates; never emits production footfall fields."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

try:
    from common import TOURISM_DATA_SEOUL_SIGNGU
    from indicator_engine import calculate_indicators
    from oa21285_history import DEFAULT_HISTORY_DIR, summarize_district_history
    from oa21285_percentile import compute_district_population_percentiles
except ImportError:  # pragma: no cover
    from .common import TOURISM_DATA_SEOUL_SIGNGU
    from .indicator_engine import calculate_indicators
    from .oa21285_history import DEFAULT_HISTORY_DIR, summarize_district_history
    from .oa21285_percentile import compute_district_population_percentiles


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {"count": len(values), "mean": round(statistics.mean(values), 2) if values else None,
            "stddev": round(statistics.pstdev(values), 2) if values else None,
            "min": round(min(values), 2) if values else None, "max": round(max(values), 2) if values else None}


def _indicator_preview(avg: float | None, maximum: float | None = None, spike: float | None = None) -> dict[str, int] | None:
    if avg is None:
        return None
    empty = {"count": 0, "items": []}
    data = {"query": {}, "weather": {}, "special_day": dict(empty), "festival": dict(empty),
            "event": dict(empty), "performance": dict(empty), "sports": dict(empty),
            "overview": {"content_tag_counts": {}}, "source_status": {},
            "footfall": {"items": [], "summary": {"visitor_avg_in_band": avg,
                "visitor_max_in_band": maximum if maximum is not None else avg,
                "visitor_spike_ratio": spike if spike is not None else 1.0}}}
    result = calculate_indicators(data)
    return {"inflow": result.inflow_pressure, "spending": result.spending_intent,
            "competition": result.competition_pressure, "risk": result.operational_risk}


def build_calibration_diagnostics(snapshot: dict[str, Any], *, sdot: dict[str, dict[str, Any]] | None = None,
                                  history_dir: str | Path | None = None) -> dict[str, Any]:
    raw = {district: float(block["district_population"]) for district, block in (snapshot.get("districts") or {}).items()
           if isinstance(block.get("district_population"), (int, float))}
    low, high = (min(raw.values()), max(raw.values())) if raw else (None, None)
    span = high - low if low is not None and high is not None else None
    minmax = {district: round((value - low) / span * 100, 2) if span else 50.0 for district, value in raw.items()}
    percentile_records = compute_district_population_percentiles({district: {"district_population": value} for district, value in raw.items()})
    percentile = {district: item["population_percentile"] for district, item in percentile_records.items()}
    date = str(snapshot.get("population_time") or "")[:10]
    hour = int(str(snapshot.get("population_time") or "")[11:13] or 0)
    time_band = "심야" if hour < 6 else "아침" if hour < 12 else "점심" if hour < 17 else "오후" if hour < 20 else "저녁"
    districts = {}
    for district in TOURISM_DATA_SEOUL_SIGNGU:
        value = raw.get(district)
        history = summarize_district_history(district, date, time_band, history_dir=history_dir)
        historical_avg = history.get("visitor_avg_in_band") if history.get("valid_snapshot_count", 0) >= 2 else None
        historical_max = history.get("visitor_max_in_band") if history.get("valid_snapshot_count", 0) >= 2 else None
        districts[district] = {
            "source_status": "ok" if value is not None else "no_data", "raw_oa_population": value,
            "minmax_0_100": minmax.get(district), "percentile_0_100": percentile.get(district),
            "sdot_raw": (sdot or {}).get(district), "oa_raw_existing_formula_preview": _indicator_preview(value),
            "candidate_relative_difference": round(percentile[district] - minmax[district], 2) if district in raw else None,
            "time_change": {"current_population": value, "time_band_avg_population": historical_avg,
                "time_band_max_population": historical_max,
                "current_to_historical_avg": round(value / historical_avg, 4) if value is not None and historical_avg else None,
                "max_to_average": round(historical_max / historical_avg, 4) if historical_avg and historical_max else None,
                "valid_snapshot_count": history.get("valid_snapshot_count", 0)},
        }
    raw_values, mm_values, pct_values = list(raw.values()), list(minmax.values()), list(percentile.values())
    return {"policy_status": "diagnostics_only", "population_time": snapshot.get("population_time"),
            "usable_district_count": len(raw), "no_data_districts": [d for d in TOURISM_DATA_SEOUL_SIGNGU if d not in raw],
            "distributions": {"raw": _distribution(raw_values), "minmax": _distribution(mm_values),
                              "percentile": _distribution(pct_values)}, "districts": districts}


def main() -> None:
    parser = argparse.ArgumentParser(description="OA-21285 calibration diagnostics (no production writes)")
    parser.add_argument("snapshot")
    parser.add_argument("--history-dir", default=None)
    parser.add_argument("--sdot-json", default=None)
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    sdot = json.loads(Path(args.sdot_json).read_text(encoding="utf-8")) if args.sdot_json else None
    print(json.dumps(build_calibration_diagnostics(snapshot, sdot=sdot, history_dir=args.history_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
