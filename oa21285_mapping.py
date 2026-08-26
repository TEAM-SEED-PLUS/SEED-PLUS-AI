"""OA-21285 POI와 자치구 geometry의 공간 교차 후보 생성기."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Iterable

try:
    from oa21285_config import OA21285_MIN_INTERSECTION_RATIO, OA21285_SINGLE_DISTRICT_RATIO
except ImportError:  # pragma: no cover
    from .oa21285_config import OA21285_MIN_INTERSECTION_RATIO, OA21285_SINGLE_DISTRICT_RATIO


GeometryFactory = Callable[[dict[str, Any]], Any]


def _default_geometry_factory(geometry: dict[str, Any]) -> Any:
    try:
        from shapely.geometry import shape
        from shapely.validation import make_valid
    except ImportError as exc:  # pragma: no cover - 선택 GIS dependency
        raise RuntimeError("GeoJSON 공간 조인 실행에는 shapely가 필요합니다.") from exc
    parsed = shape(geometry)
    return parsed if parsed.is_valid else make_valid(parsed)


def generate_spatial_mapping(
    poi_features: Iterable[dict[str, Any]], district_features: Iterable[dict[str, Any]], *,
    geometry_factory: GeometryFactory | None = None,
    min_intersection_ratio: float = OA21285_MIN_INTERSECTION_RATIO,
    single_district_ratio: float = OA21285_SINGLE_DISTRICT_RATIO,
) -> dict[str, Any]:
    """교차 후보와 v1 단일 귀속 자치구를 함께 반환한다."""
    factory = geometry_factory or _default_geometry_factory
    districts = []
    for feature in district_features:
        props = feature.get("properties", {}) or {}
        name = str(props.get("district") or props.get("SIG_KOR_NM") or props.get("name") or "")
        if name and feature.get("geometry"):
            districts.append((name, factory(feature["geometry"])))

    mappings = []
    category_counts: Counter[str] = Counter()
    district_candidate_counts: Counter[str] = Counter()
    for feature in poi_features:
        props = feature.get("properties", {}) or {}
        area_cd = str(props.get("area_cd") or props.get("AREA_CD") or "")
        area_nm = str(props.get("area_nm") or props.get("AREA_NM") or "")
        category = str(props.get("category") or props.get("CATEGORY") or "")
        if category:
            category_counts[category] += 1
        geometry_error = None
        try:
            geometry = factory(feature["geometry"]) if feature.get("geometry") else None
        except Exception as exc:
            geometry = None
            geometry_error = f"{type(exc).__name__}: {exc}"
        area = float(geometry.area) if geometry is not None else 0.0
        raw_candidates = []
        if area > 0:
            for district, district_geometry in districts:
                intersection_area = float(geometry.intersection(district_geometry).area)
                if intersection_area <= 0:
                    continue
                ratio = max(0.0, min(1.0, intersection_area / area))
                raw_candidates.append({"district": district, "intersection_ratio": round(ratio, 6)})
        raw_candidates.sort(key=lambda item: (-item["intersection_ratio"], item["district"]))
        effective_candidates = [
            item for item in raw_candidates if item["intersection_ratio"] >= min_intersection_ratio
        ]
        ignored_slivers = [
            item for item in raw_candidates if item["intersection_ratio"] < min_intersection_ratio
        ]
        for item in effective_candidates:
            district_candidate_counts[item["district"]] += 1
        count = len(effective_candidates)
        out_of_service_area = geometry is not None and area > 0 and len(raw_candidates) == 0
        top_ratio = effective_candidates[0]["intersection_ratio"] if effective_candidates else None
        tied = bool(len(effective_candidates) > 1 and effective_candidates[1]["intersection_ratio"] == top_ratio)
        assigned_district = None if tied or geometry is None or area <= 0 else (
            effective_candidates[0]["district"] if effective_candidates else None
        )
        single_candidate = (
            count == 1 and effective_candidates[0]["intersection_ratio"] >= single_district_ratio
        )
        mappings.append({
            "area_cd": area_cd, "area_nm": area_nm, "category": category,
            "raw_candidates": raw_candidates,
            "effective_candidates": effective_candidates,
            "ignored_sliver_candidates": ignored_slivers,
            # 기존 소비자를 위한 alias이며 effective 후보를 가리킨다.
            "district_candidates": effective_candidates,
            "raw_candidate_count": len(raw_candidates),
            "candidate_count": count,
            "single_district_candidate": single_candidate,
            "single_district": single_candidate,
            "cross_boundary": count > 1,
            "unmatched": count == 0,
            "out_of_service_area": out_of_service_area,
            "assigned_district": assigned_district,
            "assignment_status": (
                "invalid_geometry" if geometry is None or area <= 0 else "out_of_service_area" if out_of_service_area else
                "ambiguous_tie" if tied else "assigned" if assigned_district else "unmatched"
            ),
            "geometry_diagnostics": {"valid": geometry is not None and area > 0, "error": geometry_error},
        })

    cross_boundary = [item for item in mappings if item["cross_boundary"]]
    partial_single = [
        item for item in mappings
        if item["candidate_count"] == 1 and not item["single_district"]
    ]
    diagnostics = {
        "total_poi_count": len(mappings),
        "single_district_count": sum(bool(item["single_district"]) for item in mappings),
        "cross_boundary_count": len(cross_boundary),
        "unmatched_count": sum(bool(item["unmatched"]) for item in mappings),
        "out_of_service_area_count": sum(bool(item["out_of_service_area"]) for item in mappings),
        "raw_cross_boundary_count": sum(item["raw_candidate_count"] > 1 for item in mappings),
        "ignored_sliver_candidate_count": sum(len(item["ignored_sliver_candidates"]) for item in mappings),
        "partial_single_candidate_count": len(partial_single),
        "min_intersection_ratio": min_intersection_ratio,
        "single_district_ratio": single_district_ratio,
        "district_candidate_poi_counts": dict(sorted(district_candidate_counts.items())),
        "category_poi_counts": dict(sorted(category_counts.items())),
        "cross_boundary_pois": cross_boundary,
        "partial_single_candidate_pois": partial_single,
        "ambiguous_assignment_pois": [item for item in mappings if item["assignment_status"] in {"ambiguous_tie", "invalid_geometry"}],
        "district_assigned_poi_counts": dict(sorted(Counter(
            item["assigned_district"] for item in mappings if item.get("assigned_district")
        ).items())),
    }
    return {"mappings": mappings, "diagnostics": diagnostics}
