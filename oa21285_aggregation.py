"""OA-21285 official v1 district aggregation and diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

try:
    from oa21285_config import OA21285_CATEGORY_WEIGHTS, OA21285_EXCLUDED_CATEGORIES, OA21285_PRODUCTION_AGGREGATION_ENABLED
except ImportError:  # pragma: no cover
    from .oa21285_config import OA21285_CATEGORY_WEIGHTS, OA21285_EXCLUDED_CATEGORIES, OA21285_PRODUCTION_AGGREGATION_ENABLED


@dataclass(frozen=True)
class OA21285AggregationPolicy:
    included_categories: tuple[str, ...] = tuple(OA21285_CATEGORY_WEIGHTS)
    cross_boundary_strategy: str = "max_effective_intersection_single_district"
    population_value_strategy: str = "min_max_midpoint"
    multi_poi_strategy: str = "population_and_category_weighted_mean"
    production_enabled: bool = OA21285_PRODUCTION_AGGREGATION_ENABLED

    @property
    def status(self) -> str:
        return "official_v1"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "status": self.status}


def production_aggregation_state(policy: OA21285AggregationPolicy | None = None) -> dict[str, Any]:
    selected = policy or OA21285AggregationPolicy()
    return {"production_enabled": selected.production_enabled, "policy": selected.to_dict(), "district_values": None}


def _assigned_district(mapping: dict[str, Any]) -> tuple[str | None, str]:
    if mapping.get("out_of_service_area"):
        return None, "out_of_service_area"
    if "assigned_district" in mapping:
        return mapping.get("assigned_district"), str(mapping.get("assignment_status") or "unmatched")
    candidates = mapping.get("effective_candidates") or mapping.get("district_candidates") or []
    if not candidates:
        return None, "unmatched"
    ordered = sorted(candidates, key=lambda item: (-float(item.get("intersection_ratio") or 0), str(item.get("district") or "")))
    if len(ordered) > 1 and float(ordered[0].get("intersection_ratio") or 0) == float(ordered[1].get("intersection_ratio") or 0):
        return None, "ambiguous_tie"
    return str(ordered[0].get("district") or "") or None, "assigned"


def aggregate_district_populations(mappings: Iterable[dict[str, Any]], place_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate each usable POI once using midpoint * category weight as its weight."""
    results = {str(item.get("area_cd") or ""): item for item in place_results}
    contributions: dict[str, list[dict[str, Any]]] = {}
    excluded, assignment_issues = [], []
    assigned_districts: set[str] = set()
    for mapping in mappings:
        area_cd = str(mapping.get("area_cd") or "")
        category = str(mapping.get("category") or "")
        district, assignment_status = _assigned_district(mapping)
        if category in OA21285_EXCLUDED_CATEGORIES or category not in OA21285_CATEGORY_WEIGHTS:
            excluded.append({"area_cd": area_cd, "category": category, "reason": "excluded_category"})
            continue
        if not district:
            assignment_issues.append({"area_cd": area_cd, "status": assignment_status})
            continue
        assigned_districts.add(district)
        result = results.get(area_cd) or {}
        population = result.get("population") or (result.get("current") or {}).get("population") or {}
        low, high = population.get("min"), population.get("max")
        if result.get("source_status") != "ok" or not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            continue
        representative = (float(low) + float(high)) / 2
        category_weight = float(OA21285_CATEGORY_WEIGHTS[category])
        final_weight = representative * category_weight
        contributions.setdefault(district, []).append({
            "area_cd": area_cd, "area_nm": mapping.get("area_nm") or result.get("area_nm"), "category": category,
            "population_min": low, "population_max": high, "representative_population": representative,
            "category_weight": category_weight, "final_weight": final_weight,
            "replace_yn": result.get("replace_yn") or (result.get("current") or {}).get("replace_yn"),
            "population_time": result.get("population_time"),
        })

    districts = {}
    for district in sorted(assigned_districts):
        samples = contributions.get(district, [])
        denominator = sum(item["final_weight"] for item in samples)
        population = sum(item["representative_population"] * item["final_weight"] for item in samples) / denominator if denominator else None
        districts[district] = {
            "district_population": round(population, 2) if population is not None else None,
            "district_max_population_reference": max((item["population_max"] for item in samples), default=None),
            "poi_count": len(samples), "source_status": "ok" if population is not None else "no_data",
            "contributions": samples,
        }
    return {"policy_status": "official_v1", "districts": districts,
            "diagnostics": {"excluded_pois": excluded, "assignment_issues": assignment_issues}}


def district_coverage_diagnostics(mappings: Iterable[dict[str, Any]], place_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    statuses = {str(item.get("area_cd") or ""): str(item.get("source_status") or "no_data") for item in place_results}
    district_codes: dict[str, set[str]] = {}
    for mapping in mappings:
        district, _ = _assigned_district(mapping)
        code = str(mapping.get("area_cd") or "")
        if district and code:
            district_codes.setdefault(district, set()).add(code)
    diagnostics = {}
    for district, codes in sorted(district_codes.items()):
        counts = {"ok": 0, "no_data": 0, "failed": 0}
        for code in codes:
            status = statuses.get(code, "no_data")
            counts[status if status in counts else "failed"] += 1
        expected = len(codes)
        diagnostics[district] = {"expected_poi_count": expected, "ok_poi_count": counts["ok"],
            "no_data_poi_count": counts["no_data"], "failed_poi_count": counts["failed"],
            "coverage_ratio": round(counts["ok"] / expected, 6) if expected else None}
    return diagnostics


def preview_aggregation_experiments(district: str, mappings: Iterable[dict[str, Any]], place_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Backward-compatible diagnostic wrapper around official v1 aggregation."""
    aggregated = aggregate_district_populations(mappings, place_results)
    block = aggregated["districts"].get(district) or {}
    return {"production_enabled": OA21285_PRODUCTION_AGGREGATION_ENABLED, "official_value": block.get("district_population"),
            "district": district, "experiments": {}, "sample_count": block.get("poi_count", 0),
            "samples": block.get("contributions", [])}
