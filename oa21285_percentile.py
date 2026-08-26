"""Reproducible same-snapshot Seoul district population percentiles."""

from __future__ import annotations

from typing import Any


def compute_district_population_percentiles(districts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return 0..100 average-rank percentiles; missing populations stay no_data/null."""
    values = {}
    for district, source in districts.items():
        value = source.get("district_population") if isinstance(source, dict) else source
        if isinstance(value, (int, float)):
            values[str(district)] = float(value)
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    denominator = len(ordered) - 1
    percentiles: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = (index + end) / 2
        percentile = rank / denominator * 100 if denominator > 0 else 50.0
        for offset in range(index, end + 1):
            percentiles[ordered[offset][0]] = round(percentile, 2)
        index = end + 1
    return {str(district): {
        "population": values.get(str(district)),
        "population_percentile": percentiles.get(str(district)),
        "source_status": "ok" if str(district) in values else "no_data",
    } for district in districts}
