"""Read-only budget comparisons for consumption calibration; never production scoring."""

from __future__ import annotations

import statistics
from typing import Any


def summarize(values: dict[str, float]) -> dict[str, Any]:
    ordered = sorted(values.values())
    return {"min": min(ordered), "median": statistics.median(ordered), "max": max(ordered),
            "mean": sum(ordered) / len(ordered), "at_or_above_100_count": sum(v >= 100 for v in ordered)}


def build_budget_preview(sales_percentiles: dict[str, float], tourism_bonuses: dict[str, float], *,
                         oa21285_percentiles: dict[str, float] | None = None,
                         realtime_percentiles: dict[str, float] | None = None) -> dict[str, Any]:
    """Compare hypothetical budgets with actual percentiles, without choosing caps."""
    oa = oa21285_percentiles or {}; realtime = realtime_percentiles or {}
    districts = {}
    distributions: dict[str, dict[str, float]] = {key: {} for key in (
        "tour_a_independent_plus10", "tour_b_reduced_cap3", "tour_c_replace_base10",
        "all_signals_independent_upper_bound")}
    for district, percentile in sales_percentiles.items():
        sales = float(percentile) / 10
        tour = float(tourism_bonuses.get(district, 0))
        footfall = float(oa.get(district, 0)) / 10
        live = float(realtime.get(district, 0)) / 10
        values = {
            "tour_a_independent_plus10": 24 + sales + tour,
            "tour_b_reduced_cap3": 24 + sales + tour * 0.3,
            "tour_c_replace_base10": 14 + sales + tour,
            "all_signals_independent_upper_bound": 24 + sales + tour + footfall + live,
        }
        districts[district] = {"sales_candidate_bonus_0_10": sales, "current_tourapi_bonus": tour,
                               "oa21285_proxy_0_10": footfall, "oa22385_proxy_0_10": live, **values}
        for key, value in values.items(): distributions[key][district] = value
    return {"hybrid_status": "preview_only", "production_formula": False,
            "warning": "OA-22385 and reduced TourAPI mappings are hypothetical calibration representations",
            "summaries": {key: summarize(value) for key, value in distributions.items()},
            "districts": districts}
