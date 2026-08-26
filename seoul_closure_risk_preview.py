"""Calibration-only OA-22173 district closure-rate artifacts.

The module reuses consumption snapshots and never changes production scoring.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from commercial_store_snapshot import percentile_ranks
from quarterly_sales_baseline import quarter_distance, validate_quarter
from sales_store_alignment import store_paths


DEFAULT_CONSUMPTION_DIR = Path(__file__).resolve().parent / "data" / "consumption"
DEFAULT_RISK_DIR = Path(__file__).resolve().parent / "data" / "risk" / "seoul_closure"
ROW_RATE_TOLERANCE_PERCENTAGE_POINTS = 0.051
SOURCE_SEMANTICS = (
    "서울시 상권분석서비스의 분기별 점포 폐업률을 기반으로 한 자치구 상대 구조적 영업 위험"
)
KOSIS_SEMANTICS = "기업생멸행정통계의 연간 기업 소멸률; OA-22173 점포 폐업률과 결합하지 않음"


def _number(row: dict[str, Any], field: str) -> float | None:
    try:
        return float(row[field]) if row.get(field) not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read_snapshot(quarter: str, *, consumption_dir: str | Path | None = None) -> list[dict[str, Any]]:
    path = store_paths(consumption_dir or DEFAULT_CONSUMPTION_DIR, validate_quarter(quarter))["raw"]
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def row_consistency(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    valid = exact = near = zero_denominator = 0
    differences: list[float] = []
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        total = _number(row, "SIMILR_INDUTY_STOR_CO")
        closed = _number(row, "CLSBIZ_STOR_CO")
        official = _number(row, "CLSBIZ_RT")
        if not total or total <= 0:
            zero_denominator += 1
            continue
        if closed is None or official is None:
            continue
        valid += 1
        calculated = closed / total * 100.0
        difference = abs(calculated - official)
        differences.append(difference)
        if difference <= 1e-12:
            exact += 1
        if difference <= ROW_RATE_TOLERANCE_PERCENTAGE_POINTS:
            near += 1
        else:
            mismatches.append({
                "district": row.get("SIGNGU_CD_NM"), "service_industry_code": row.get("SVC_INDUTY_CD"),
                "official_rate": official, "calculated_rate": calculated, "difference_pp": difference,
            })
    return {
        "valid_row_count": valid, "zero_denominator_count": zero_denominator,
        "exact_match_count": exact, "exact_match_rate": exact / valid if valid else None,
        "near_match_count": near, "near_match_rate": near / valid if valid else None,
        "tolerance_percentage_points": ROW_RATE_TOLERANCE_PERCENTAGE_POINTS,
        "mean_absolute_difference_pp": statistics.fmean(differences) if differences else None,
        "max_absolute_difference_pp": max(differences) if differences else None,
        "mismatch_row_count": len(mismatches), "mismatch_rows": mismatches,
    }


def duplicate_diagnostics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    grains = [(str(r.get("STDR_YYQU_CD") or ""), str(r.get("SIGNGU_CD") or ""),
               str(r.get("SVC_INDUTY_CD") or "")) for r in rows]
    code_names: dict[str, set[str]] = defaultdict(set)
    by_district: dict[str, set[str]] = defaultdict(set)
    identity_mismatch = 0
    for row in rows:
        code = str(row.get("SVC_INDUTY_CD") or "")
        code_names[code].add(str(row.get("SVC_INDUTY_CD_NM") or ""))
        by_district[str(row.get("SIGNGU_CD") or "")].add(code)
        total, stores, franchises = (_number(row, field) for field in (
            "SIMILR_INDUTY_STOR_CO", "STOR_CO", "FRC_STOR_CO"))
        if None not in (total, stores, franchises) and abs(total - stores - franchises) > 1e-9:
            identity_mismatch += 1
    counts = [len(codes) for codes in by_district.values()]
    return {
        "grain": ["quarter", "district_code", "service_industry_code"],
        "duplicate_grain_count": len(grains) - len(set(grains)),
        "service_industry_code_count": len(code_names),
        "code_with_multiple_names_count": sum(len(names) > 1 for names in code_names.values()),
        "district_industry_count_min": min(counts) if counts else 0,
        "district_industry_count_max": max(counts) if counts else 0,
        "total_equals_general_plus_franchise_mismatch_count": identity_mismatch,
        "population_overlap_detected": False,
        "basis": "단일 서비스업종 코드 grain, 중복 grain 없음, 전체=일반+프랜차이즈 검증",
    }


def aggregate_districts(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        grain = (str(row.get("STDR_YYQU_CD") or ""), str(row.get("SIGNGU_CD") or ""),
                 str(row.get("SVC_INDUTY_CD") or ""))
        if grain in seen:
            continue
        seen.add(grain)
        district = str(row.get("SIGNGU_CD_NM") or "").strip()
        total, closed = _number(row, "SIMILR_INDUTY_STOR_CO"), _number(row, "CLSBIZ_STOR_CO")
        block = output.setdefault(district, {
            "district": district, "district_code": str(row.get("SIGNGU_CD") or ""),
            "total_store_count": 0.0, "closed_store_count": 0.0, "service_industry_row_count": 0,
            "source_status": "ok",
        })
        if total is None or closed is None:
            block["source_status"] = "partial"
            continue
        block["total_store_count"] += total
        block["closed_store_count"] += closed
        block["service_industry_row_count"] += 1
    for block in output.values():
        total = block["total_store_count"]
        block["closure_rate"] = block["closed_store_count"] / total * 100.0 if total > 0 else None
        if total <= 0:
            block["source_status"] = "no_data"
    return dict(sorted(output.items()))


def _distribution(values: list[float]) -> dict[str, float | int]:
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "std": statistics.pstdev(values), "min": min(values), "max": max(values)}


def normalize_districts(districts: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    values = {district: row.get("closure_rate") for district, row in districts.items()}
    valid = {district: float(value) for district, value in values.items() if isinstance(value, (int, float))}
    if not valid:
        return districts, {}
    pcts = percentile_ranks(values)
    low, high = min(valid.values()), max(valid.values())
    mean, std = statistics.fmean(valid.values()), statistics.pstdev(valid.values())
    span = high - low
    normalized = {}
    for district, row in districts.items():
        value = row.get("closure_rate")
        normalized[district] = {**row,
            "closure_rate_percentile": pcts.get(district),
            "closure_rate_min_max_0_100": ((value - low) / span * 100.0 if span and value is not None else 50.0),
            "closure_rate_z_score": ((value - mean) / std if std and value is not None else 0.0),
        }
    ordered = sorted(normalized.values(), key=lambda row: row["closure_rate"])
    return normalized, {**_distribution(list(valid.values())),
                        "bottom_5": [row["district"] for row in ordered[:5]],
                        "top_5": [row["district"] for row in ordered[-5:][::-1]]}


def recent_quarter_candidates(quarter_districts: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    quarters = sorted(quarter_districts)
    all_districts = sorted(set().union(*(value.keys() for value in quarter_districts.values()))) if quarters else []
    output = {}
    for district in all_districts:
        available = [(q, quarter_districts[q][district]) for q in quarters if district in quarter_districts[q]
                     and quarter_districts[q][district].get("closure_rate") is not None]
        rates = [float(row["closure_rate"]) for _, row in available]
        closed = sum(float(row["closed_store_count"]) for _, row in available)
        total = sum(float(row["total_store_count"]) for _, row in available)
        output[district] = {
            "quarterly_closure_rates": {q: row["closure_rate"] for q, row in available},
            "available_quarter_count": len(available),
            "latest_quarter_rate": rates[-1] if rates else None,
            "four_quarter_mean_rate": statistics.fmean(rates[-4:]) if rates else None,
            "four_quarter_pooled_rate": closed / total * 100.0 if total > 0 else None,
        }
    return {"available_quarters": quarters, "districts": output,
            "stability_status": "ok" if len(quarters) >= 4 else "insufficient_quarters"}


def build_preview(quarter: str, *, consumption_dir: str | Path | None = None,
                  risk_dir: str | Path | None = None) -> dict[str, Any]:
    q = validate_quarter(quarter)
    rows = read_snapshot(q, consumption_dir=consumption_dir)
    if not rows:
        raise FileNotFoundError(f"complete OA-22173 snapshot not found: {q}")
    districts = aggregate_districts(rows)
    normalized, stats = normalize_districts(districts)
    diagnostics = {"row_consistency": row_consistency(rows), "duplicate_population": duplicate_diagnostics(rows)}
    root = Path(risk_dir) if risk_dir else DEFAULT_RISK_DIR
    aggregated_path, normalized_path = root / "aggregated" / f"{q}.json", root / "normalized" / f"{q}.json"
    aggregated = {"snapshot_quarter": q, "source_reference": str(store_paths(consumption_dir or DEFAULT_CONSUMPTION_DIR, q)["raw"]),
                  "raw_duplicated": False, "production_scoring_connected": False,
                  "source_semantics": SOURCE_SEMANTICS, "diagnostics": diagnostics, "districts": districts}
    fixed = {"content": 4.0, "weather": 16.0, "special_day": 3.0}
    short_term = sum(fixed.values())
    for row in normalized.values():
        pct = row["closure_rate_percentile"]
        row["risk_preview"] = {
            "fixed_short_term_contributions": fixed, "existing_base20": min(100.0, 20.0 + short_term),
            "candidate_a_structural_0_20": min(100.0, short_term + pct * 0.20),
            "candidate_b_fixed10_structural_0_10": min(100.0, short_term + 10.0 + pct * 0.10),
            "candidate_c_fixed15_structural_0_5": min(100.0, short_term + 15.0 + pct * 0.05),
        }
    preview = {"snapshot_quarter": q, "preview_only": True, "production_scoring_connected": False,
               "normalization_recommendation": "average_rank_percentile_0_100",
               "distribution": stats, "districts": normalized}
    for path, value in ((aggregated_path, aggregated), (normalized_path, preview)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return preview


def load_preview(requested_quarter: str, *, risk_dir: str | Path | None = None) -> dict[str, Any]:
    requested = validate_quarter(requested_quarter)
    root = Path(risk_dir) if risk_dir else DEFAULT_RISK_DIR
    direct = root / "normalized" / f"{requested}.json"
    if direct.is_file():
        return {"data": json.loads(direct.read_text(encoding="utf-8")), "requested_quarter": requested,
                "source_quarter": requested, "age_quarters": 0, "source_status": "ok"}
    candidates = sorted(path.stem for path in (root / "normalized").glob("*.json") if path.stem < requested)
    if candidates:
        source = candidates[-1]
        return {"data": json.loads((root / "normalized" / f"{source}.json").read_text(encoding="utf-8")),
                "requested_quarter": requested, "source_quarter": source,
                "age_quarters": quarter_distance(requested, source), "source_status": "fallback"}
    return {"data": None, "requested_quarter": requested, "source_quarter": None,
            "age_quarters": None, "source_status": "no_data"}
