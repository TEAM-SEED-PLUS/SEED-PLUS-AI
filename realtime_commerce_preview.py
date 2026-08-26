"""OA-22385 reference, mapping, cache, and district preview diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from commercial_store_snapshot import SEOUL_DISTRICT_CODES, percentile_ranks
from consumption_data_probe import ConsumptionDataError, SeoulConsumptionClient
from oa21285_aggregation import _assigned_district


SEOUL_TZ = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data" / "consumption"
DEFAULT_OA_PLACES = ROOT / "data" / "oa21285" / "reference" / "places_20260402.json"
DEFAULT_OA_MAPPINGS = ROOT / "data" / "oa21285" / "reference" / "mapping_candidates_20260402.json"


def _now() -> str:
    return datetime.now(SEOUL_TZ).isoformat(timespec="seconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON document: {path}")
    return value


def collect_reference(client: SeoulConsumptionClient, *, reference_date: str,
                      oa_places_path: str | Path = DEFAULT_OA_PLACES,
                      data_dir: str | Path | None = None) -> dict[str, Any]:
    """Discover commercial coverage by probing the official OA-21285 place list."""
    oa = _load(oa_places_path)
    available, statuses = [], Counter()
    for place in oa.get("places", []):
        code = str(place.get("area_cd") or "")
        try:
            result = client.realtime_commerce(code)
        except ConsumptionDataError:
            statuses["failed"] += 1
            continue
        statuses[result["source_status"]] += 1
        if result["source_status"] != "ok":
            continue
        available.append({
            "area_cd": str(result.get("area_cd") or code),
            "area_nm": str(result.get("area_nm") or place.get("area_nm") or ""),
            "category": place.get("category"), "eng_nm": place.get("eng_nm"),
            "reference_date": reference_date,
        })
    unique = {item["area_cd"]: item for item in available}
    snapshot = {
        "reference_date": reference_date, "source": "OA-22385 live coverage probe",
        "source_status": "ok" if unique else "failed",
        "candidate_place_count": len(oa.get("places", [])), "place_count": len(unique),
        "probe_status_counts": dict(statuses), "places": [unique[k] for k in sorted(unique)],
    }
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    _write_json(root / "realtime" / "reference" / f"places_{reference_date.replace('-', '')}.json", snapshot)
    return snapshot


def build_mapping_diagnostics(reference: dict[str, Any], *,
                              oa_places_path: str | Path = DEFAULT_OA_PLACES,
                              oa_mappings_path: str | Path = DEFAULT_OA_MAPPINGS,
                              data_dir: str | Path | None = None) -> dict[str, Any]:
    oa_places = {str(p["area_cd"]): p for p in _load(oa_places_path).get("places", [])}
    oa_maps = {str(m["area_cd"]): m for m in _load(oa_mappings_path).get("mappings", [])}
    mappings, mismatch, unmapped = [], [], []
    counts = Counter()
    cross_boundary = 0
    for place in reference.get("places", []):
        code = str(place.get("area_cd") or "")
        prior_place, prior_map = oa_places.get(code), oa_maps.get(code)
        if not prior_place or not prior_map:
            unmapped.append(place)
            continue
        if str(prior_place.get("area_nm") or "") != str(place.get("area_nm") or ""):
            mismatch.append({"area_cd": code, "oa21285_name": prior_place.get("area_nm"),
                             "oa22385_name": place.get("area_nm")})
        candidates = prior_map.get("effective_candidates") or prior_map.get("district_candidates") or []
        # Reuse OA-21285's verified largest-effective-intersection policy. Keep
        # cross-boundary status visible; do not invent a name-based assignment.
        assigned, assignment_status = _assigned_district(prior_map)
        cross = bool(prior_map.get("cross_boundary"))
        cross_boundary += int(cross)
        if assigned:
            counts[str(assigned)] += 1
        else:
            unmapped.append(place)
        mappings.append({
            "area_cd": code, "area_nm": place.get("area_nm"), "assigned_district": assigned,
            "mapping_source": "OA-21285 verified geometry", "assignment_status": assignment_status,
            "cross_boundary": cross,
            "effective_candidates": candidates,
        })
    diagnostics = {
        "reference_date": reference.get("reference_date"), "total_place_count": reference.get("place_count", 0),
        "oa21285_code_reuse_count": len(mappings), "name_mismatch_count": len(mismatch),
        "name_mismatches": mismatch, "unmapped_count": len(unmapped), "unmapped_places": unmapped,
        "cross_boundary_count": cross_boundary,
        "district_place_counts": {d: counts[d] for d in SEOUL_DISTRICT_CODES},
        "zero_place_districts": [d for d in SEOUL_DISTRICT_CODES if counts[d] == 0],
        "mappings": mappings,
    }
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    date = str(reference.get("reference_date") or "unknown").replace("-", "")
    _write_json(root / "realtime" / "reference" / f"mappings_{date}.json", diagnostics)
    return diagnostics


def save_realtime(result: dict[str, Any], mapping: dict[str, Any] | None, *,
                  data_dir: str | Path | None = None, received_at: datetime | None = None) -> dict[str, Any]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    received = received_at or datetime.now(SEOUL_TZ)
    commercial = result.get("commercial") if isinstance(result.get("commercial"), dict) else {}
    def number(key: str) -> float | None:
        try:
            value = commercial.get(key)
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
    minimum, maximum = number("AREA_SH_PAYMENT_AMT_MIN"), number("AREA_SH_PAYMENT_AMT_MAX")
    midpoint = (minimum + maximum) / 2 if minimum is not None and maximum is not None else None
    industries = []
    for item in commercial.get("CMRCL_RSB") or []:
        if not isinstance(item, dict):
            continue
        industries.append({key: item.get(key) for key in (
            "RSB_LRG_CTGR", "RSB_MID_CTGR", "RSB_PAYMENT_LVL", "RSB_SH_PAYMENT_CNT",
            "RSB_SH_PAYMENT_AMT_MIN", "RSB_SH_PAYMENT_AMT_MAX", "RSB_MCT_CNT", "RSB_MCT_TIME")})
    payload = {
        "area_cd": result.get("area_cd"), "area_nm": result.get("area_nm"),
        "source_timestamp": commercial.get("CMRCL_TIME"), "received_at": received.isoformat(timespec="seconds"),
        "commercial_level": commercial.get("AREA_CMRCL_LVL"),
        "payment_count": number("AREA_SH_PAYMENT_CNT"), "payment_amount_min": minimum,
        "payment_amount_max": maximum, "payment_amount_midpoint": midpoint,
        "industry_commerce": industries,
        "district_mapping": mapping, "source_status": result.get("source_status", "failed"),
    }
    code = str(payload.get("area_cd") or "unknown")
    latest = root / "realtime" / "latest" / f"{code}.json"
    history = root / "realtime" / "history" / received.strftime("%Y-%m-%d") / received.strftime("%H%M") / f"{code}.json"
    prior = _load(latest) if latest.is_file() else {}
    duplicate_time = bool(payload["source_timestamp"] and prior.get("source_timestamp") == payload["source_timestamp"])
    _write_json(latest, payload)
    if not duplicate_time and not history.is_file():
        _write_json(history, payload)
    return {"latest": latest, "history": history, "history_created": not duplicate_time}


def load_realtime_latest(area_cd: str, *, data_dir: str | Path | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    path = root / "realtime" / "latest" / f"{area_cd}.json"
    if not path.is_file():
        return {"data": None, "source_status": "no_data", "latest_source_time": None,
                "received_at": None, "age_minutes": None}
    payload = _load(path)
    received_text = str(payload.get("received_at") or "")
    try:
        received = datetime.fromisoformat(received_text)
        current = now or datetime.now(SEOUL_TZ)
        age = max(0.0, (current - received).total_seconds() / 60)
    except (ValueError, TypeError):
        age = None
    return {
        "data": payload, "source_status": payload.get("source_status", "failed"),
        "latest_source_time": payload.get("source_timestamp"), "received_at": payload.get("received_at"),
        "age_minutes": round(age, 2) if isinstance(age, (int, float)) else None,
    }


def observed_levels(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    for result in results:
        commercial = result.get("commercial") if isinstance(result.get("commercial"), dict) else {}
        level = commercial.get("AREA_CMRCL_LVL")
        if level:
            counts[str(level)] += 1
    return {
        "observed_unique_values": sorted(counts), "counts": dict(counts),
        "official_order": ["한산한", "보통", "바쁜", "분주한"],
        "numeric_mapping_applied": False,
        "meaning": "동일 장소·업종의 과거 동요일·동시간대 결제량 기준 상권 활성 수준",
    }


def build_district_realtime_preview(results: Iterable[dict[str, Any]], mapping_doc: dict[str, Any]) -> dict[str, Any]:
    mapping = {str(m["area_cd"]): m for m in mapping_doc.get("mappings", [])}
    assigned = Counter(str(m.get("assigned_district")) for m in mapping.values() if m.get("assigned_district"))
    values: dict[str, list[str]] = {district: [] for district in SEOUL_DISTRICT_CODES}
    for result in results:
        code = str(result.get("area_cd") or "")
        district = (mapping.get(code) or {}).get("assigned_district")
        commercial = result.get("commercial") if isinstance(result.get("commercial"), dict) else {}
        level = commercial.get("AREA_CMRCL_LVL")
        if district and level:
            values[district].append(str(level))
    districts = {}
    for district in SEOUL_DISTRICT_CODES:
        levels = values[district]
        districts[district] = {
            "assigned_place_count": assigned[district], "valid_place_count": len(levels),
            "observed_commercial_levels": levels,
            "simple_average_candidate": None, "oa21285_population_weighted_candidate": None,
            "place_percentile_aggregation_candidate": None,
            "source_status": "ok" if levels else "no_data",
        }
    return {
        "source": "OA-22385", "hybrid_status": "preview_only", "production_aggregation_selected": False,
        "aggregation_candidates": ["simple_average", "oa21285_current_population_weighted_average",
                                   "valid_place_percentile_rank_aggregation"],
        "districts": districts,
    }


def build_payment_aggregation_preview(snapshots: Iterable[dict[str, Any]], mapping_doc: dict[str, Any],
                                      population_by_area: dict[str, float] | None = None) -> dict[str, Any]:
    """Compare aggregation candidates without selecting a production metric."""
    mapping = {str(m["area_cd"]): m for m in mapping_doc.get("mappings", [])}
    population = population_by_area or {}
    valid = [s for s in snapshots if isinstance(s.get("payment_amount_midpoint"), (int, float))]
    amount_pct = percentile_ranks({str(s["area_cd"]): s.get("payment_amount_midpoint") for s in valid})
    count_pct = percentile_ranks({str(s["area_cd"]): s.get("payment_count") for s in valid})
    buckets: dict[str, list[dict[str, Any]]] = {d: [] for d in SEOUL_DISTRICT_CODES}
    for snapshot in valid:
        code = str(snapshot.get("area_cd") or "")
        district = (mapping.get(code) or {}).get("assigned_district")
        if district:
            item = dict(snapshot); item["amount_percentile"] = amount_pct.get(code)
            item["count_percentile"] = count_pct.get(code); item["population_weight"] = population.get(code)
            buckets[district].append(item)
    districts = {}
    for district, items in buckets.items():
        amounts = [float(x["payment_amount_midpoint"]) for x in items]
        weights = [x.get("population_weight") for x in items]
        weighted_pairs = [(float(item["payment_amount_midpoint"]), float(item["population_weight"]))
                          for item in items if isinstance(item.get("population_weight"), (int, float))
                          and float(item["population_weight"]) >= 0]
        weight_total = sum(weight for _, weight in weighted_pairs)
        districts[district] = {
            "assigned_place_count": sum(1 for m in mapping.values() if m.get("assigned_district") == district),
            "valid_place_count": len(items),
            "population_weighted_valid_place_count": len(weighted_pairs),
            "candidate_a_amount_simple_mean": sum(amounts) / len(amounts) if amounts else None,
            "candidate_b_population_weighted_mean": (
                sum(amount * weight for amount, weight in weighted_pairs) / weight_total
                if weight_total > 0 else None),
            "candidate_c_amount_percentile_mean": (
                sum(float(x["amount_percentile"]) for x in items) / len(items) if items else None),
            "candidate_d_count_amount_percentile_mean": (
                sum((float(x["amount_percentile"]) + float(x["count_percentile"])) / 2 for x in items) / len(items)
                if items and all(x.get("count_percentile") is not None for x in items) else None),
            "source_status": "ok" if items else "no_data",
        }
    return {"hybrid_status": "preview_only", "production_metric_selected": False,
            "districts": districts}


def same_band_history_preview(current: dict[str, Any], history: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute ratios only from caller-selected same-band history snapshots."""
    prior = [item for item in history if item.get("area_cd") == current.get("area_cd") and
             item.get("source_timestamp") != current.get("source_timestamp")]
    amounts = [float(x["payment_amount_midpoint"]) for x in prior
               if isinstance(x.get("payment_amount_midpoint"), (int, float))]
    counts = [float(x["payment_count"]) for x in prior if isinstance(x.get("payment_count"), (int, float))]
    amount_avg = sum(amounts) / len(amounts) if amounts else None
    count_avg = sum(counts) / len(counts) if counts else None
    amount = current.get("payment_amount_midpoint"); count = current.get("payment_count")
    return {"current_payment_amount": amount, "same_band_history_avg": amount_avg,
            "payment_amount_ratio": amount / amount_avg if isinstance(amount, (int, float)) and amount_avg else None,
            "payment_count_current": count, "payment_count_history_avg": count_avg,
            "payment_count_ratio": count / count_avg if isinstance(count, (int, float)) and count_avg else None,
            "history_snapshot_count": len(prior), "source_status": "ok" if prior else "no_data",
            "preview_only": True}


def build_hybrid_preview(district: str, baseline: dict[str, Any] | None,
                         realtime: dict[str, Any] | None) -> dict[str, Any]:
    baseline = baseline or {}
    realtime = realtime or {}
    baseline_ok = bool(baseline)
    realtime_ok = realtime.get("source_status") == "ok"
    status = "hybrid_preview" if baseline_ok and realtime_ok else (
        "baseline_only" if baseline_ok else "realtime_only_candidate" if realtime_ok else "existing_spending_only")
    return {
        "district": district,
        "baseline": {
            "quarter": baseline.get("snapshot_quarter"),
            "sales_raw": baseline.get("district_total_estimated_sales"),
            "sales_per_store": baseline.get("estimated_sales_per_store"),
            "percentile": baseline.get("sales_per_store_percentile"),
            "source_status": "ok" if baseline_ok else "failed",
        },
        "realtime": {
            "source": "OA-22385", "place_count": realtime.get("valid_place_count", 0),
            "commercial_level": realtime.get("observed_commercial_levels", []),
            "source_status": realtime.get("source_status", "failed"),
        },
        "fallback_mode": status, "hybrid_status": "preview_only",
    }
