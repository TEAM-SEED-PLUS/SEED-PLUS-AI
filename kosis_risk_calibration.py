"""Diagnostics for annual KOSIS structural-risk candidates; no score caps."""

from __future__ import annotations

import math
import statistics
from typing import Any


def distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("values are required")
    return {
        "count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
        "std": statistics.pstdev(values), "min": min(values), "max": max(values),
    }


def calibration_rows(rows: list[dict[str, Any]], field: str = "enterprise_death_rate_pct") -> list[dict[str, Any]]:
    values = [float(row[field]) for row in rows]
    stats = distribution(values)
    ordered = sorted(set(values))
    span = float(stats["max"]) - float(stats["min"])
    std = float(stats["std"])
    result = []
    for row, value in zip(rows, values):
        below = sum(other < value for other in values)
        equal = sum(other == value for other in values)
        percentile = 100.0 * (below + 0.5 * equal) / len(values)
        result.append({
            **row,
            "percentile_midrank": percentile,
            "z_score": (value - float(stats["mean"])) / std if std else 0.0,
            "min_max_0_10": 10.0 * (value - float(stats["min"])) / span if span else 5.0,
        })
    return result
