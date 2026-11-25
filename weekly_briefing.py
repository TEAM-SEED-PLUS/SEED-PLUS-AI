"""Deterministic aggregation of Hongdae daily snapshots."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from snapshot_cache import read_snapshot, write_snapshot
from location_context import HONGDAE_DEFAULT
from weekly_briefing_renderer import render_weekly_briefing, generate_verified_narrative
from common import TIME_BANDS
from hongdae_briefing_sections import environments, vitality
from location_context import LocationContext


def _factor_counts(rows: list[dict], positive: bool) -> list[str]:
    keys = Counter()
    for row in rows:
        indicators = row.get("indicators", {})
        for key, value in indicators.items():
            if isinstance(value, (int, float)) and ((positive and value >= 60) or (not positive and value <= 40)):
                keys[key] += 1
    return [name for name, _ in keys.most_common(3)]


def aggregate_weekly(snapshots: list[dict[str, Any]], start: str, end: str,
                     previous_snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [x for x in snapshots if isinstance(x.get("opportunity_score"), (int, float))
            and start <= x.get("date", "") <= end
            and x.get("data_quality", {}).get("status") != "no_data"]
    ordered = sorted(rows, key=lambda x: (x.get("date", ""), x.get("time_band", "")))
    best = max(ordered, key=lambda x: x["opportunity_score"], default=None)
    worst = min(ordered, key=lambda x: x["opportunity_score"], default=None)
    bands: dict[str, list[float]] = defaultdict(list)
    for row in ordered:
        bands[str(row.get("time_band"))].append(float(row["opportunity_score"]))
    best_band = max(bands, key=lambda k: sum(bands[k]) / len(bands[k])) if bands else None
    average = round(sum(float(x["opportunity_score"]) for x in ordered) / len(ordered), 1) if ordered else None
    previous = [x for x in (previous_snapshots or []) if isinstance(x.get("opportunity_score"), (int, float))]
    previous_average = (round(sum(float(x["opportunity_score"]) for x in previous) / len(previous), 1)
                        if previous else None)
    daily = defaultdict(list)
    daily_population = defaultdict(dict)
    population_bands = defaultdict(list)
    for row in ordered:
        daily[row["date"]].append(row["opportunity_score"])
        signal = row.get("dynamic_signals", {}).get("living_population", {})
        if signal.get("population") is not None:
            population_bands[row["time_band"]].append(signal["population"])
            daily_population[row["date"]][row["time_band"]] = signal
    daily_scores = [{"date": day, "weekday": date.fromisoformat(day).weekday(),
                     "score": round(mean(scores), 1),
                     "population_by_time_band": {band: round(signal["population"], 2)
                                                 for band, signal in daily_population[day].items()},
                     "mean_hourly_population": (
                         round(mean(hourly), 2) if (hourly := [
                             value for signal in daily_population[day].values()
                             for value in signal.get("hourly_population", {}).values()]) else None
                     )} for day, scores in sorted(daily.items())]
    average = round(mean(day["score"] for day in daily_scores), 1) if daily_scores else None

    def complete_week(entries):
        observed = defaultdict(set)
        for row in entries:
            population = row.get("dynamic_signals", {}).get("living_population", {})
            if population.get("source_status") == "ok":
                observed[row["date"]].add(row["time_band"])
        return len(observed) == 7 and all(len(bands) == 5 for bands in observed.values())

    complete = complete_week(ordered)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    expected = [(first + timedelta(days=index)).isoformat()
                for index in range((last - first).days + 1)]
    missing = {day: [band for band in TIME_BANDS if daily_population[day].get(band, {}).get("source_status") != "ok"]
               for day in expected}
    population_days = [row for row in daily_scores if row["mean_hourly_population"] is not None]
    active = max(population_days, key=lambda row: row["mean_hourly_population"], default=None)
    quiet = min(population_days, key=lambda row: row["mean_hourly_population"], default=None)
    band_means = {band: round(mean(values), 1) for band, values in population_bands.items()}
    current_hours = [value for signals in daily_population.values() for signal in signals.values()
                     for value in signal.get("hourly_population", {}).values()]
    previous_signals = [row.get("dynamic_signals", {}).get("living_population", {}) for row in previous]
    previous_hours = [value for signal in previous_signals
                      for value in signal.get("hourly_population", {}).values()]
    grains = {signal.get("spatial_grain") for signals in daily_population.values() for signal in signals.values()}
    previous_grains = {signal.get("spatial_grain") for signal in previous_signals}
    population_change = (round((mean(current_hours) / mean(previous_hours) - 1) * 100, 2)
                         if complete and complete_week(previous) and len(grains) == 1 and grains == previous_grains
                         and len(current_hours) == len(previous_hours) == 168 and mean(previous_hours) > 0 else None)
    prior = {(row["date"], row["time_band"]): row for row in previous}
    matches = []
    for row in ordered:
        day, band = row["date"], row["time_band"]
        previous_day = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
        other = prior.get((previous_day, band))
        left = daily_population[day].get(band, {})
        right = other.get("dynamic_signals", {}).get("living_population", {}) if other else {}
        if (left.get("spatial_grain") == right.get("spatial_grain") == "radius_grid_estimate"
                and left.get("source_status") == right.get("source_status") == "ok"
                and right["population"] > 0 and row.get("location") == other.get("location")):
            matches.append({"date": day, "previous_date": previous_day, "time_band": band,
                            "population": left["population"], "previous_population": right["population"],
                            "population_change_pct": round((left["population"] / right["population"] - 1) * 100, 2),
                            "opportunity_change_points": round(row["opportunity_score"] - other["opportunity_score"], 2)})
    latest = ordered[-1] if ordered else {}
    baseline = latest.get("structural_baseline", {})
    if not baseline:
        from hongdae_data import quarterly_baseline
        baseline = quarterly_baseline(HONGDAE_DEFAULT, end) or {}
    location = latest.get("location") or HONGDAE_DEFAULT.to_dict()
    if location.get("latitude") is not None and location.get("longitude") is not None:
        from hongdae_store_file import radius_inventory
        inventory = radius_inventory(LocationContext(**{key: location[key] for key in
            ("latitude", "longitude", "radius_m", "place_name") if key in location}))
        if inventory:
            baseline = {**baseline, "radius_inventory_reference": {
                **{key: value for key, value in inventory.items() if key != "stores"},
                "usage": "structural_reference_only_not_used_in_historical_score"}}
    result = {"location": latest.get("location", HONGDAE_DEFAULT.to_dict()), "period": {"start": start, "end": end},
            "weekly_average_score": average,
            "week_over_week": round(average - previous_average, 1)
            if complete and complete_week(previous) and previous_average is not None and len(matches) == 35 else None,
            "best_day": ({"date": best.get("date"), "time_band": best.get("time_band"),
                          "score": best.get("opportunity_score")} if best else {}),
            "worst_day": ({"date": worst.get("date"), "time_band": worst.get("time_band"),
                           "score": worst.get("opportunity_score")} if worst else {}),
            "best_time_band": best_band,
            "daily_scores": daily_scores,
            "main_positive_factors": _factor_counts(ordered, True),
            "main_negative_factors": _factor_counts(ordered, False),
            "structural_baseline": baseline,
            "data_through": max((x.get("date", "") for x in ordered), default=""),
            "source_period": baseline.get("metadata", {}).get("source_period"),
            "completeness": "complete" if complete else "partial",
            "data_quality": {"status": "ok" if complete else "partial", "snapshot_count": len(ordered),
                             "missing_time_bands": {day: bands for day, bands in missing.items() if bands},
                             "population_by_time_band": band_means,
                             "most_active_population_day": active["date"] if active else None,
                             "quietest_population_day": quiet["date"] if quiet else None,
                             "strongest_population_band": max(band_means, key=band_means.get) if band_means else None,
                             "weakest_population_band": min(band_means, key=band_means.get) if band_means else None,
                             "population_spatial_grain": next(iter(grains)) if len(grains) == 1 else ("mixed" if grains else None),
                             "population_week_over_week_pct": population_change,
                             "weekly_vitality": {"method": "equal_weight_same_weekday_and_time_band",
                                 "matched_bands": len(matches), "expected_bands": len(expected) * len(TIME_BANDS),
                                 "status": "complete" if len(matches) == len(expected) * len(TIME_BANDS) else "partial",
                                 "population_change_pct": round(mean(x["population_change_pct"] for x in matches), 2) if matches else None,
                                 "opportunity_change_points": round(mean(x["opportunity_change_points"] for x in matches), 2) if matches else None,
                                 "matches": matches},
                             "score_status": "provisional" if ordered else "no_data",
                             "historical_events_status": "no_data"}}
    result["weekly_vitality"] = vitality(result, expected, daily_population, prior, matches)
    result["structural_environment"], result["nearby_competition"] = environments(location, baseline)
    result["narrative"] = {"generation_mode": "deterministic_fallback", "summary": render_weekly_briefing(result)}
    return result


def load_week(start: str, end: str, daily_dir: str | Path = "data/hongdae/daily") -> list[dict]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    rows: list[dict] = []
    current = first
    while current <= last:
        for path in Path(daily_dir).glob(f"{current.isoformat()}_*.json"):
            payload = read_snapshot(path)
            if payload:
                rows.append(payload)
        current += timedelta(days=1)
    return rows


def build_weekly_briefing(start: str, end: str, *, daily_dir: str | Path = "data/hongdae/daily",
                          output_dir: str | Path = "data/hongdae/weekly", use_llm: bool = False) -> dict:
    previous_start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
    previous_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    result = aggregate_weekly(load_week(start, end, daily_dir), start, end,
                              load_week(previous_start, previous_end, daily_dir))
    if use_llm:
        result["narrative"] = generate_verified_narrative(result)
    write_snapshot(Path(output_dir) / f"{start}_{end}.json", result)
    return result
