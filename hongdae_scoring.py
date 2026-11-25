"""Weather-free scoring profile. Existing district scoring is intentionally untouched."""
from __future__ import annotations

from typing import Any

WEIGHTS = {"inflow_pressure": 0.35, "spending_intent": 0.30,
           "competition_pressure": 0.15, "operational_risk": 0.20}


def _bounded(value: Any, default: float = 50.0) -> float:
    return round(max(0.0, min(100.0, float(value if isinstance(value, (int, float)) else default))), 1)


def calculate_hongdae_indicators(dynamic: dict, baseline: dict, radius_stores: dict) -> dict:
    population = dynamic.get("living_population", {}).get("population")
    content_count = int(dynamic.get("content", {}).get("count") or 0)
    special = bool(dynamic.get("special_day", {}).get("is_special_day"))
    inflow = _bounded(50 + min(content_count, 10) * 3 + (10 if special else 0) +
                      (min(float(population) / 1000, 20) if isinstance(population, (int, float)) else 0))
    sales = baseline.get("estimated_sales")
    spending = _bounded(50 if sales is None else 50 + min(float(sales) / 100_000_000, 25))
    stores = radius_stores.get("total_store_count")
    competition = _bounded(50 if radius_stores.get("source_status") != "ok" else min(100, 30 + stores / 5))
    closure = baseline.get("closure_rate")
    # Raw closure is exposed, but not scored without a maintained percentile and sample eligibility.
    risk = _bounded(50)
    indicators = {"inflow_pressure": inflow, "spending_intent": spending,
                  "competition_pressure": competition, "operational_risk": risk}
    contributions = {
        "inflow_pressure": {"population": population, "content_count": content_count, "special_day": special},
        "spending_intent": {"quarterly_estimated_sales": sales},
        "competition_pressure": {"radius_store_count": stores},
        "operational_risk": {"closure_rate_raw_not_scored": closure, "applied_signals": []},
    }
    score = round(sum(indicators[k] * weight for k, weight in WEIGHTS.items()), 1)
    return {"profile": "hongdae_no_weather", "indicators": indicators,
            "algorithm_version": "hongdae_no_weather-v1",
            "observed_indicator_weight": round(
                WEIGHTS["inflow_pressure"] * bool(population is not None or content_count or special)
                + WEIGHTS["spending_intent"] * (sales is not None)
                + WEIGHTS["competition_pressure"] * (radius_stores.get("source_status") == "ok"), 2),
            "evidence": {
                "inflow_pressure": {key: dynamic.get("living_population", {}).get(key)
                                    for key in ("source", "source_time", "source_status", "spatial_grain")},
                "spending_intent": {"source_period": baseline.get("metadata", {}).get("source_period"),
                                    "missing": sales is None},
                "competition_pressure": {"source_status": radius_stores.get("source_status"),
                                         "collected_at": radius_stores.get("collected_at")},
                "operational_risk": {"missing": True, "reason": "no_eligible_short_term_signal"},
            },
            "normalization": {"inflow_pressure": "50 + min(events,10)*3 + holiday*10 + min(population/1000,20)",
                              "spending_intent": "50 + min(quarterly_sales_KRW/100000000,25)",
                              "competition_pressure": "min(100,30 + radius_store_count/5)",
                              "operational_risk": "50; no eligible short-term signal"},
            "missing_data_policy": "Missing inputs add zero to base 50; unavailable store count uses 50.",
            "weighted_points": {key: round(indicators[key] * weight, 3) for key, weight in WEIGHTS.items()},
            "weights": WEIGHTS, "contributions": contributions, "opportunity_score": score}
