"""Quarterly SEMAS commercial-store snapshots and preview diagnostics.

This layer is intentionally disconnected from ``indicator_engine``.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from commercial_store_api import (
    MAX_ROWS_PER_REQUEST,
    CommercialStoreAPIError,
    CommercialStoreClient,
    load_service_key,
)


SEOUL_TZ = ZoneInfo("Asia/Seoul")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data" / "commercial_stores"

# Current Korean administrative codes.  Do not derive these by appending a digit:
# Gangbuk, Gwangjin and Geumcheon use the half-step code suffix 5.
SEOUL_DISTRICT_CODES = {
    "강남구": "11680", "강동구": "11740", "강북구": "11305", "강서구": "11500",
    "관악구": "11620", "광진구": "11215", "구로구": "11530", "금천구": "11545",
    "노원구": "11350", "도봉구": "11320", "동대문구": "11230", "동작구": "11590",
    "마포구": "11440", "서대문구": "11410", "서초구": "11650", "성동구": "11200",
    "성북구": "11290", "송파구": "11710", "양천구": "11470", "영등포구": "11560",
    "용산구": "11170", "은평구": "11380", "종로구": "11110", "중구": "11140",
    "중랑구": "11260",
}

# Seoul Statistics district-area reference (km²).  Geometry was not retained in
# this repository.  ``areas_from_geojson`` is provided for replacing this table
# with a versioned boundary snapshot later.
SEOUL_DISTRICT_AREA_KM2 = {
    "강남구": 39.50, "강동구": 24.59, "강북구": 23.60, "강서구": 41.45,
    "관악구": 29.57, "광진구": 17.06, "구로구": 20.12, "금천구": 13.02,
    "노원구": 35.44, "도봉구": 20.65, "동대문구": 14.22, "동작구": 16.35,
    "마포구": 23.85, "서대문구": 17.63, "서초구": 46.98, "성동구": 16.86,
    "성북구": 24.58, "송파구": 33.88, "양천구": 17.41, "영등포구": 24.55,
    "용산구": 21.87, "은평구": 29.71, "종로구": 23.91, "중구": 9.96,
    "중랑구": 18.50,
}

RAW_FIELDS = (
    "bizesId", "bizesNm", "indsLclsCd", "indsLclsNm", "indsMclsCd", "indsMclsNm",
    "indsSclsCd", "indsSclsNm", "ctprvnCd", "ctprvnNm", "signguCd", "signguNm",
    "adongCd", "adongNm", "ldongCd", "ldongNm",
    # Current live names plus compatibility-only names if a future response actually supplies them.
    "rdnmAdr", "lnoAdr", "roadNmAddr", "lnmAddr", "lon", "lat",
)


def _now() -> str:
    return datetime.now(SEOUL_TZ).isoformat(timespec="seconds")


def validate_quarter(value: str) -> str:
    text = str(value).strip().upper()
    if len(text) != 6 or text[:4].isdigit() is False or text[4] != "Q" or text[5] not in "1234":
        raise ValueError("snapshot quarter must look like 2026Q3")
    return text


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def snapshot_paths(data_dir: str | Path | None, quarter: str) -> dict[str, Path]:
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    q = validate_quarter(quarter)
    return {
        "root": root,
        "raw": root / "raw" / q,
        "checkpoints": root / "checkpoints" / q,
        "aggregated": root / "aggregated" / f"{q}.json",
        "taxonomy": root / "taxonomy" / f"{q}.json",
        "scoring": root / "scoring" / f"{q}.json",
        "preview": root / "preview" / f"{q}.json",
        "manifest": root / "manifest.json",
    }


def normalize_store(record: dict[str, Any]) -> dict[str, Any]:
    """Preserve the verified API field names without inventing unavailable fields."""
    return {field: record.get(field) for field in RAW_FIELDS if field in record}


def _append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(normalize_store(record), ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def compact_district_jsonl(path: Path) -> dict[str, int]:
    temporary = path.with_suffix(".jsonl.tmp")
    seen: set[str] = set()
    raw_count = duplicate_count = deduplicated_count = 0
    with temporary.open("w", encoding="utf-8") as output:
        for record in _read_jsonl(path):
            raw_count += 1
            store_id = str(record.get("bizesId") or "").strip()
            if store_id and store_id in seen:
                duplicate_count += 1
                continue
            if store_id:
                seen.add(store_id)
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            deduplicated_count += 1
    temporary.replace(path)
    return {"raw_count": raw_count, "deduplicated_count": deduplicated_count, "duplicate_count": duplicate_count}


def _request_with_retry(
    operation: Callable[[], dict[str, Any]], *, retries: int, backoff_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    last_error: CommercialStoreAPIError | None = None
    for attempt in range(retries + 1):
        try:
            return operation()
        except CommercialStoreAPIError as exc:
            last_error = exc
            if attempt < retries:
                sleep(backoff_seconds * (2 ** attempt))
    raise last_error or CommercialStoreAPIError("unknown collection failure")


def collect_district(
    client: CommercialStoreClient,
    quarter: str,
    district: str,
    *,
    data_dir: str | Path | None = None,
    retries: int = 2,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    paths = snapshot_paths(data_dir, quarter)
    if district not in SEOUL_DISTRICT_CODES:
        raise ValueError(f"unsupported Seoul district: {district}")
    raw_path = paths["raw"] / f"{district}.jsonl"
    checkpoint_path = paths["checkpoints"] / f"{district}.json"
    checkpoint = _read_json(checkpoint_path) or {
        "district": district, "district_code": SEOUL_DISTRICT_CODES[district],
        "status": "collecting", "expected_pages": None, "collected_pages": [],
        "failed_pages": [], "api_call_count": 0, "started_at": _now(),
    }
    if checkpoint.get("status") == "complete" and raw_path.is_file():
        return checkpoint

    expected_pages = checkpoint.get("expected_pages")
    collected = {int(page) for page in checkpoint.get("collected_pages", [])}
    failed: set[int] = set()
    pages_to_collect = [1] if expected_pages is None else [
        page for page in range(1, int(expected_pages) + 1) if page not in collected
    ]
    for page_no in pages_to_collect:
        attempts = 0
        def request_page(p: int = page_no) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            return client.stores_in_dong(
                "signguCd", SEOUL_DISTRICT_CODES[district], page_no=p,
                rows=MAX_ROWS_PER_REQUEST,
            )
        try:
            result = _request_with_retry(
                request_page,
                retries=retries, backoff_seconds=backoff_seconds, sleep=sleep,
            )
        except CommercialStoreAPIError as exc:
            checkpoint["api_call_count"] = int(checkpoint.get("api_call_count", 0)) + attempts
            failed.add(page_no)
            checkpoint["last_error"] = str(exc)
            continue
        checkpoint["api_call_count"] = int(checkpoint.get("api_call_count", 0)) + attempts
        if expected_pages is None:
            expected_pages = max(1, math.ceil(result["total_count"] / MAX_ROWS_PER_REQUEST))
            checkpoint["expected_pages"] = expected_pages
            # Page one establishes the full range; collect remaining pages now.
            pages_to_collect.extend(range(2, expected_pages + 1))
        _append_jsonl(raw_path, result["items"])
        collected.add(page_no)
        checkpoint.update({
            "collected_pages": sorted(collected), "failed_pages": sorted(failed),
            "expected_total_count": result["total_count"], "updated_at": _now(),
        })
        _write_json(checkpoint_path, checkpoint)

    expected_pages = int(expected_pages or 0)
    missing = sorted(set(range(1, expected_pages + 1)) - collected) if expected_pages else [1]
    if missing:
        checkpoint.update({"status": "partial" if collected else "failed", "failed_pages": missing, "updated_at": _now()})
    else:
        stats = compact_district_jsonl(raw_path)
        checkpoint.update({
            "status": "complete", "failed_pages": [], "completed_at": _now(), **stats,
        })
    _write_json(checkpoint_path, checkpoint)
    return checkpoint


def percentile_ranks(values: dict[str, float | int | None]) -> dict[str, float | None]:
    """Generic 0..100 average-rank percentile used by preview diagnostics."""
    valid = sorted(
        ((key, float(value)) for key, value in values.items() if isinstance(value, (int, float))),
        key=lambda item: (item[1], item[0]),
    )
    output: dict[str, float | None] = {key: None for key in values}
    denominator = len(valid) - 1
    index = 0
    while index < len(valid):
        end = index
        while end + 1 < len(valid) and valid[end + 1][1] == valid[index][1]:
            end += 1
        value = ((index + end) / 2 / denominator * 100) if denominator > 0 else 50.0
        for position in range(index, end + 1):
            output[valid[position][0]] = round(value, 2)
        index = end + 1
    return output


def _category_key(record: dict[str, Any], prefix: str) -> tuple[str, str] | None:
    code = str(record.get(f"inds{prefix}clsCd") or "").strip()
    if not code:
        return None
    return code, str(record.get(f"inds{prefix}clsNm") or "").strip()


def build_snapshot_artifacts(
    quarter: str, *, data_dir: str | Path | None = None,
    district_areas: dict[str, float] | None = None,
) -> dict[str, Any]:
    paths = snapshot_paths(data_dir, quarter)
    areas = district_areas or SEOUL_DISTRICT_AREA_KM2
    district_rows: dict[str, dict[str, Any]] = {}
    taxonomy_counts = {"large": Counter(), "middle": Counter(), "small": Counter()}
    metrics = Counter()
    level_specs = {"large": "L", "middle": "M", "small": "S"}

    for district in SEOUL_DISTRICT_CODES:
        checkpoint = _read_json(paths["checkpoints"] / f"{district}.json") or {"status": "failed"}
        records = list(_read_jsonl(paths["raw"] / f"{district}.jsonl"))
        counts = {level: Counter() for level in level_specs}
        for record in records:
            metrics["raw_record_count"] += 1
            if not record.get("lat") or not record.get("lon"):
                metrics["null_coordinate_count"] += 1
            if not record.get("indsSclsCd"):
                metrics["null_industry_count"] += 1
            if str(record.get("signguNm") or "") not in SEOUL_DISTRICT_CODES:
                metrics["unknown_district_count"] += 1
            for level, prefix in level_specs.items():
                key = _category_key(record, prefix)
                if key:
                    counts[level][key] += 1
                    taxonomy_counts[level][key] += 1
        total = len(records)
        area = areas.get(district)
        density = round(total / area, 2) if total and area else None
        district_rows[district] = {
            "district": district, "district_code": SEOUL_DISTRICT_CODES[district],
            "source_status": checkpoint.get("status", "failed"),
            "total_store_count": total, "district_area_km2": area,
            "stores_per_km2": density,
            "industry_counts": {
                level: {code: {"name": name, "count": count} for (code, name), count in sorted(counter.items())}
                for level, counter in counts.items()
            },
            "quality": {key: checkpoint.get(key) for key in (
                "expected_pages", "collected_pages", "raw_count", "deduplicated_count", "duplicate_count"
            )},
        }

    count_percentiles = percentile_ranks({d: row["total_store_count"] for d, row in district_rows.items()})
    density_percentiles = percentile_ranks({d: row["stores_per_km2"] for d, row in district_rows.items()})
    for district, row in district_rows.items():
        row["total_store_count_percentile"] = count_percentiles[district]
        row["stores_per_km2_percentile"] = density_percentiles[district]

    taxonomy = {
        "snapshot_quarter": validate_quarter(quarter),
        **{
            level: [
                {"code": code, "name": name, "store_count": count}
                for (code, name), count in sorted(counter.items())
            ] for level, counter in taxonomy_counts.items()
        },
        "diagnostics": {f"{level}_count": len(counter) for level, counter in taxonomy_counts.items()},
    }
    aggregate = {"snapshot_quarter": validate_quarter(quarter), "districts": district_rows}
    _write_json(paths["aggregated"], aggregate)
    _write_json(paths["taxonomy"], taxonomy)

    ranked_count = sorted(district_rows.values(), key=lambda x: x["total_store_count"], reverse=True)
    ranked_density = sorted(district_rows.values(), key=lambda x: x["stores_per_km2"] or -1, reverse=True)
    count_rank = {row["district"]: rank for rank, row in enumerate(ranked_count, 1)}
    density_rank = {row["district"]: rank for rank, row in enumerate(ranked_density, 1)}
    preview_rows = [{
        "district": d, "store_count": row["total_store_count"], "area_km2": row["district_area_km2"],
        "stores_per_km2": row["stores_per_km2"],
        "count_percentile": row["total_store_count_percentile"],
        "density_percentile": row["stores_per_km2_percentile"],
        "count_rank": count_rank[d], "density_rank": density_rank[d],
        "rank_difference": abs(count_rank[d] - density_rank[d]),
    } for d, row in district_rows.items()]
    preview = {
        "snapshot_quarter": validate_quarter(quarter), "diagnostics_only": True,
        "rows": sorted(preview_rows, key=lambda x: x["district"]),
        "top_store_count": [x["district"] for x in ranked_count[:5]],
        "bottom_store_count": [x["district"] for x in ranked_count[-5:]],
        "top_density": [x["district"] for x in ranked_density[:5]],
        "bottom_density": [x["district"] for x in ranked_density[-5:]],
        "largest_rank_differences": sorted(preview_rows, key=lambda x: (-x["rank_difference"], x["district"]))[:10],
    }
    _write_json(paths["preview"], preview)
    return {"aggregate": aggregate, "taxonomy": taxonomy, "preview": preview, "metrics": dict(metrics)}


def _manifest_entry(quarter: str, paths: dict[str, Path], started_at: str, artifacts: dict[str, Any]) -> dict[str, Any]:
    checkpoints = [
        _read_json(paths["checkpoints"] / f"{district}.json") or {"status": "failed"}
        for district in SEOUL_DISTRICT_CODES
    ]
    statuses = Counter(str(item.get("status") or "failed") for item in checkpoints)
    complete = statuses["complete"]
    partial = statuses["partial"]
    failed = statuses["failed"] + statuses["collecting"]
    raw = sum(int(item.get("raw_count") or item.get("expected_total_count") or 0) for item in checkpoints)
    deduped = sum(int(item.get("deduplicated_count") or 0) for item in checkpoints)
    duplicates = sum(int(item.get("duplicate_count") or 0) for item in checkpoints)
    source_status = "complete" if complete == len(SEOUL_DISTRICT_CODES) else "partial" if complete or partial else "failed"
    taxonomy_diag = artifacts["taxonomy"]["diagnostics"]
    return {
        "snapshot_id": f"commercial-stores-{quarter}", "snapshot_quarter": quarter,
        "started_at": started_at, "completed_at": _now() if source_status == "complete" else None,
        "district_count": len(SEOUL_DISTRICT_CODES), "completed_district_count": complete,
        "partial_district_count": partial, "failed_district_count": failed,
        "api_call_count": sum(int(item.get("api_call_count") or 0) for item in checkpoints),
        "raw_record_count": raw, "deduplicated_record_count": deduped,
        "duplicate_count": duplicates,
        "null_coordinate_count": artifacts["metrics"].get("null_coordinate_count", 0),
        "null_industry_count": artifacts["metrics"].get("null_industry_count", 0),
        "unknown_district_count": artifacts["metrics"].get("unknown_district_count", 0),
        **taxonomy_diag, "source_status": source_status,
    }


def collect_seoul_snapshot(
    quarter: str,
    *,
    service_key: str | None = None,
    data_dir: str | Path | None = None,
    client: CommercialStoreClient | None = None,
    districts: Iterable[str] | None = None,
) -> dict[str, Any]:
    q = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, q)
    manifest = _read_json(paths["manifest"]) or {"snapshots": {}}
    prior = (manifest.get("snapshots") or {}).get(q) or {}
    started_at = str(prior.get("started_at") or _now())
    api = client or CommercialStoreClient(service_key or load_service_key())
    for district in districts or SEOUL_DISTRICT_CODES:
        collect_district(api, q, district, data_dir=data_dir)
    artifacts = build_snapshot_artifacts(q, data_dir=data_dir)
    entry = _manifest_entry(q, paths, started_at, artifacts)
    manifest.setdefault("snapshots", {})[q] = entry
    manifest["latest_successful_quarter"] = q if entry["source_status"] == "complete" else manifest.get("latest_successful_quarter")
    _write_json(paths["manifest"], manifest)
    return entry


def collect_count_snapshot(
    quarter: str,
    *,
    service_key: str | None = None,
    data_dir: str | Path | None = None,
    client: CommercialStoreClient | None = None,
    retries: int = 2,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    district_areas: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Collect the production scoring snapshot in about one API call per district."""
    q = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, q)
    api = client or CommercialStoreClient(service_key or load_service_key())
    areas = district_areas or SEOUL_DISTRICT_AREA_KM2
    started_at = _now()
    calls = successful = failed = 0
    districts: dict[str, dict[str, Any]] = {}
    for district, code in SEOUL_DISTRICT_CODES.items():
        attempts = 0
        def request_count(c: str = code) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            return api.stores_in_dong("signguCd", c, page_no=1, rows=1)
        try:
            result = _request_with_retry(
                request_count, retries=retries, backoff_seconds=backoff_seconds, sleep=sleep,
            )
        except CommercialStoreAPIError:
            calls += attempts
            failed += 1
            districts[district] = {
                "store_count": None, "district_area_km2": areas.get(district),
                "stores_per_km2": None, "density_percentile": None, "source_status": "failed",
            }
            continue
        calls += attempts
        successful += 1
        count = int(result["total_count"])
        area = areas.get(district)
        density = round(count / area, 4) if isinstance(area, (int, float)) and area > 0 else None
        districts[district] = {
            "store_count": count, "district_area_km2": area,
            "stores_per_km2": density, "density_percentile": None,
            "source_status": "ok" if density is not None else "no_data",
        }
    percentiles = percentile_ranks({d: row["stores_per_km2"] for d, row in districts.items()})
    for district, row in districts.items():
        row["density_percentile"] = percentiles[district]
    source_status = "complete" if successful == len(SEOUL_DISTRICT_CODES) and all(
        row["density_percentile"] is not None for row in districts.values()
    ) else "partial" if successful else "failed"
    payload = {
        "snapshot_quarter": q, "collected_at": _now(), "started_at": started_at,
        "district_count": len(SEOUL_DISTRICT_CODES), "api_call_count": calls,
        "successful_district_count": successful, "failed_district_count": failed,
        "source_status": source_status,
        "closure_filter_applied": False,
        "closure_filter_reason": "business_registration_number_unavailable",
        "districts": districts,
    }
    _write_json(paths["scoring"], payload)
    return payload


def quarter_distance(requested: str, source: str) -> int:
    rq, sq = validate_quarter(requested), validate_quarter(source)
    return (int(rq[:4]) * 4 + int(rq[5])) - (int(sq[:4]) * 4 + int(sq[5]))


def load_snapshot(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    requested = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, requested)
    direct = _read_json(paths["aggregated"])
    manifest = _read_json(paths["manifest"]) or {"snapshots": {}}
    direct_status = ((manifest.get("snapshots") or {}).get(requested) or {}).get("source_status")
    if direct is not None and direct_status == "complete":
        return {"data": direct, "requested_quarter": requested, "source_quarter": requested,
                "source_status": "complete", "age_quarters": 0}
    candidates = []
    for path in paths["aggregated"].parent.glob("*.json") if paths["aggregated"].parent.exists() else []:
        source = path.stem
        status = ((manifest.get("snapshots") or {}).get(source) or {}).get("source_status")
        if source < requested and status == "complete":
            candidates.append(source)
    if candidates:
        source = max(candidates)
        return {"data": _read_json(snapshot_paths(data_dir, source)["aggregated"]),
                "requested_quarter": requested, "source_quarter": source,
                "source_status": "fallback", "age_quarters": quarter_distance(requested, source)}
    return {"data": direct, "requested_quarter": requested,
            "source_quarter": requested if direct else None,
            "source_status": "partial" if direct else "failed", "age_quarters": 0 if direct else None}


def load_scoring_snapshot(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    """Load requested count-only snapshot or the latest prior complete snapshot."""
    requested = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, requested)
    direct = _read_json(paths["scoring"])
    if direct and direct.get("source_status") == "complete":
        return {"data": direct, "requested_quarter": requested, "source_quarter": requested,
                "source_status": "complete", "age_quarters": 0}
    candidates = []
    scoring_dir = paths["scoring"].parent
    for path in scoring_dir.glob("*.json") if scoring_dir.exists() else []:
        value = _read_json(path)
        if path.stem < requested and value and value.get("source_status") == "complete":
            candidates.append(path.stem)
    if candidates:
        source = max(candidates)
        return {"data": _read_json(snapshot_paths(data_dir, source)["scoring"]),
                "requested_quarter": requested, "source_quarter": source,
                "source_status": "fallback", "age_quarters": quarter_distance(requested, source)}
    return {"data": direct, "requested_quarter": requested,
            "source_quarter": requested if direct else None,
            "source_status": "partial" if direct else "failed", "age_quarters": 0 if direct else None}


def build_scoring_monitoring(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    """Read-only monitoring view; never calls the commercial-store API."""
    loaded = load_scoring_snapshot(quarter, data_dir=data_dir)
    snapshot = loaded.get("data") or {}
    districts = {}
    for district, row in (snapshot.get("districts") or {}).items():
        percentile = row.get("density_percentile")
        bonus = max(0.0, min(12.0, float(percentile) / 100 * 12.0)) if isinstance(percentile, (int, float)) else None
        districts[district] = {
            "store_count": row.get("store_count"), "stores_per_km2": row.get("stores_per_km2"),
            "density_percentile": percentile, "commercial_store_bonus": round(bonus, 4) if bonus is not None else None,
            "source_status": row.get("source_status"),
        }
    return {
        **{key: loaded.get(key) for key in ("requested_quarter", "source_quarter", "age_quarters", "source_status")},
        "snapshot_status": snapshot.get("source_status"), "district_count": len(districts),
        "closure_filter_applied": False,
        "closure_filter_reason": "business_registration_number_unavailable",
        "districts": districts,
    }


def get_district_industry_count(
    snapshot: dict[str, Any], district: str, industry_level: str, industry_code: str,
) -> dict[str, Any]:
    if industry_level not in {"large", "middle", "small"}:
        raise ValueError("industry_level must be large, middle, or small")
    row = ((snapshot.get("districts") or {}).get(district) or {})
    total = int(row.get("total_store_count") or 0)
    category = (((row.get("industry_counts") or {}).get(industry_level) or {}).get(industry_code) or {})
    count = int(category.get("count") or 0)
    return {"district": district, "industry_level": industry_level, "industry_code": industry_code,
            "industry_store_count": count, "district_total_store_count": total,
            "same_industry_ratio": round(count / total, 8) if total else None}


def areas_from_geojson(path: str | Path, district_property: str = "district") -> dict[str, float]:
    """Calculate geodesic areas when a versioned Seoul boundary file is supplied."""
    try:
        from pyproj import Geod
        from shapely.geometry import shape
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("requirements-geo.txt dependencies are required") from exc
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    geod = Geod(ellps="WGS84")
    result = {}
    for feature in payload.get("features", []):
        name = str((feature.get("properties") or {}).get(district_property) or "")
        if name in SEOUL_DISTRICT_CODES:
            area_m2, _ = geod.geometry_area_perimeter(shape(feature.get("geometry")))
            result[name] = round(abs(area_m2) / 1_000_000, 4)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="분기별 서울 상가정보 snapshot batch collector")
    parser.add_argument("--quarter", required=True, help="예: 2026Q3")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--district", action="append", choices=sorted(SEOUL_DISTRICT_CODES))
    parser.add_argument("--count-only", action="store_true", help="production scoring용 25-call snapshot")
    args = parser.parse_args()
    result = collect_count_snapshot(args.quarter, data_dir=args.data_dir) if args.count_only else (
        collect_seoul_snapshot(args.quarter, data_dir=args.data_dir, districts=args.district)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
