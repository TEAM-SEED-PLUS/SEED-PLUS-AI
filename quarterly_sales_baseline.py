"""OA-22176 quarterly sales snapshot and calibration preview.

This data layer is deliberately disconnected from production scoring.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from commercial_store_snapshot import SEOUL_DISTRICT_AREA_KM2, percentile_ranks
from consumption_data_probe import ConsumptionDataError, SeoulConsumptionClient


SEOUL_TZ = ZoneInfo("Asia/Seoul")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data" / "consumption"
GRAIN_FIELDS = ("STDR_YYQU_CD", "SIGNGU_CD", "SVC_INDUTY_CD")


def _now() -> str:
    return datetime.now(SEOUL_TZ).isoformat(timespec="seconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def api_quarter_to_label(value: str) -> str:
    text = str(value).strip()
    if len(text) != 5 or not text.isdigit() or text[-1] not in "1234":
        raise ValueError("OA-22176 quarter must look like 20261")
    return f"{text[:4]}Q{text[-1]}"


def validate_quarter(value: str) -> str:
    text = str(value).strip().upper()
    if len(text) != 6 or not text[:4].isdigit() or text[4] != "Q" or text[5] not in "1234":
        raise ValueError("quarter must look like 2026Q1")
    return text


def label_to_api_quarter(value: str) -> str:
    text = validate_quarter(value)
    return text[:4] + text[5]


def snapshot_paths(data_dir: str | Path | None, quarter: str) -> dict[str, Path]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    q = validate_quarter(quarter)
    return {
        "root": root, "raw": root / "baseline" / "raw" / f"{q}.jsonl",
        "checkpoint": root / "baseline" / "checkpoints" / f"{q}.json",
        "aggregated": root / "baseline" / "aggregated" / f"{q}.json",
        "normalized": root / "baseline" / "normalized" / f"{q}.json",
        "manifest": root / "manifest.json",
    }


def grain_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(field) or "").strip() for field in GRAIN_FIELDS)  # type: ignore[return-value]


def _request_with_retry(operation: Callable[[], dict[str, Any]], retries: int,
                        backoff_seconds: float, sleep: Callable[[float], None]) -> dict[str, Any]:
    last: ConsumptionDataError | None = None
    for attempt in range(retries + 1):
        try:
            return operation()
        except ConsumptionDataError as exc:
            last = exc
            if attempt < retries:
                sleep(backoff_seconds * (2 ** attempt))
    raise last or ConsumptionDataError("unknown OA-22176 collection failure")


def collect_quarterly_snapshot(client: SeoulConsumptionClient, quarter: str, *,
                               data_dir: str | Path | None = None, page_size: int = 1000,
                               retries: int = 2, backoff_seconds: float = 0.5,
                               sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Collect all service pages, retaining only the requested quarter.

    The API does not implement a server-side quarter filter, so pagination must
    cover its whole result set. A checkpoint records completed pages for resume.
    """
    q = validate_quarter(quarter)
    source_q = label_to_api_quarter(q)
    paths = snapshot_paths(data_dir, q)
    checkpoint = _read_json(paths["checkpoint"]) or {
        "requested_quarter": q, "source_quarter": q, "status": "collecting",
        "completed_pages": [], "failed_pages": [], "started_at": _now(), "api_call_count": 0,
    }
    completed = {int(x) for x in checkpoint.get("completed_pages", [])}
    total_count = checkpoint.get("api_total_count")
    end_pages = 1 if total_count is None else max(1, (int(total_count) + page_size - 1) // page_size)
    page = 1
    raw_by_grain: dict[tuple[str, str, str], dict[str, Any]] = {}
    duplicate_count = 0
    if paths["raw"].is_file():
        for line in paths["raw"].read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                key = grain_key(row)
                if key in raw_by_grain:
                    duplicate_count += 1
                else:
                    raw_by_grain[key] = row
    failed: set[int] = set()
    while page <= end_pages:
        if page in completed:
            page += 1
            continue
        attempts = 0
        def operation() -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            start = (page - 1) * page_size + 1
            return client.estimated_sales_page(start, start + page_size - 1)
        try:
            response = _request_with_retry(operation, retries, backoff_seconds, sleep)
        except ConsumptionDataError as exc:
            checkpoint["api_call_count"] = int(checkpoint.get("api_call_count") or 0) + attempts
            checkpoint["last_error"] = str(exc)
            failed.add(page)
            page += 1
            continue
        checkpoint["api_call_count"] = int(checkpoint.get("api_call_count") or 0) + attempts
        if total_count is None:
            total_count = int(response.get("total_count") or 0)
            checkpoint["api_total_count"] = total_count
            end_pages = max(1, (total_count + page_size - 1) // page_size)
        for row in response.get("items", []):
            if str(row.get("STDR_YYQU_CD") or "") != source_q:
                continue
            key = grain_key(row)
            if key in raw_by_grain:
                duplicate_count += 1
                continue
            raw_by_grain[key] = row
        completed.add(page)
        checkpoint.update({"completed_pages": sorted(completed), "updated_at": _now()})
        _write_json(paths["checkpoint"], checkpoint)
        page += 1

    expected = set(range(1, end_pages + 1))
    missing = sorted(expected - completed)
    paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    temporary = paths["raw"].with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for key in sorted(raw_by_grain):
            handle.write(json.dumps(raw_by_grain[key], ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(paths["raw"])
    checkpoint.update({
        "status": "partial" if missing and raw_by_grain else "failed" if missing else "complete",
        "source_status": "partial" if missing and raw_by_grain else "failed" if missing else "ok",
        "failed_pages": missing, "raw_row_count": len(raw_by_grain),
        "duplicate_grain_count": duplicate_count, "completed_at": _now() if not missing else None,
    })
    _write_json(paths["checkpoint"], checkpoint)
    return checkpoint


def _number(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def aggregate_quarter(quarter: str, *, data_dir: str | Path | None = None,
                      store_snapshot_path: str | Path | None = None,
                      district_areas: dict[str, float] | None = None) -> dict[str, Any]:
    q = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, q)
    stores_path = Path(store_snapshot_path) if store_snapshot_path else (
        Path(__file__).resolve().parent / "data" / "commercial_stores" / "aggregated" / "2026Q3.json")
    stores = _read_json(stores_path) or {}
    store_districts = stores.get("districts") or {}
    areas = district_areas or SEOUL_DISTRICT_AREA_KM2
    rows: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()
    duplicates = 0
    if paths["raw"].is_file():
        for line in paths["raw"].read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or str(row.get("STDR_YYQU_CD") or "") != label_to_api_quarter(q):
                continue
            key = grain_key(row)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            district = str(row.get("SIGNGU_CD_NM") or "").strip()
            if district and not district.endswith("구"):
                district += "구"
            block = rows.setdefault(district, {
                "district": district, "district_code": str(row.get("SIGNGU_CD") or ""),
                "district_total_estimated_sales": 0, "district_total_estimated_transactions": 0,
                "industry_count": 0,
            })
            block["district_total_estimated_sales"] += _number(row, "THSMON_SELNG_AMT")
            block["district_total_estimated_transactions"] += _number(row, "THSMON_SELNG_CO")
            block["industry_count"] += 1
    for district, block in rows.items():
        store = store_districts.get(district) or {}
        count = store.get("total_store_count")
        area = areas.get(district)
        sales = block["district_total_estimated_sales"]
        block.update({
            "district_total_store_count": count if isinstance(count, (int, float)) and count > 0 else None,
            "district_area_km2": area if isinstance(area, (int, float)) and area > 0 else None,
            "estimated_sales_per_store": sales / count if isinstance(count, (int, float)) and count > 0 else None,
            "estimated_sales_per_km2": sales / area if isinstance(area, (int, float)) and area > 0 else None,
            "source_status": "ok",
        })
    aggregate = {
        "quarterly_sales_baseline": True, "snapshot_quarter": q,
        "grain": ["quarter", "district", "service_industry_code"],
        "district_count": len(rows), "raw_row_count": len(seen),
        "duplicate_grain_count": duplicates, "districts": dict(sorted(rows.items())),
    }
    _write_json(paths["aggregated"], aggregate)
    return aggregate


def build_calibration_preview(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    q = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, q)
    aggregate = _read_json(paths["aggregated"])
    if not aggregate:
        raise FileNotFoundError(f"aggregated quarterly baseline not found: {q}")
    districts = aggregate.get("districts") or {}
    specs = {
        "total_sales": "district_total_estimated_sales",
        "sales_per_store": "estimated_sales_per_store",
        "sales_per_km2": "estimated_sales_per_km2",
    }
    pct = {name: percentile_ranks({d: row.get(field) for d, row in districts.items()})
           for name, field in specs.items()}
    ranks: dict[str, dict[str, int | None]] = {}
    for name, field in specs.items():
        valid = sorted(((d, row.get(field)) for d, row in districts.items()
                        if isinstance(row.get(field), (int, float))), key=lambda x: (-x[1], x[0]))
        ranks[name] = {district: rank for rank, (district, _) in enumerate(valid, 1)}
    preview_rows = {}
    for district, row in districts.items():
        preview_rows[district] = {
            **row,
            **{f"{name}_percentile": pct[name][district] for name in specs},
            **{f"{name}_rank": ranks[name].get(district) for name in specs},
        }
    preview = {
        "snapshot_quarter": q, "hybrid_status": "preview_only", "production_scoring_connected": False,
        "source_metadata": {"source_status": "ok", "requested_quarter": q,
                            "source_quarter": q, "age_quarters": 0},
        "candidate_metrics": list(specs), "percentile_method": "average_rank_0_100",
        "min_max_usage": "diagnostics_only", "districts": preview_rows,
    }
    _write_json(paths["normalized"], preview)
    return preview


def quarter_distance(requested: str, source: str) -> int:
    rq, sq = validate_quarter(requested), validate_quarter(source)
    return (int(rq[:4]) * 4 + int(rq[5])) - (int(sq[:4]) * 4 + int(sq[5]))


def load_quarterly_baseline(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    requested = validate_quarter(quarter)
    paths = snapshot_paths(data_dir, requested)
    direct = _read_json(paths["normalized"])
    if direct:
        return {"data": direct, "requested_quarter": requested, "source_quarter": requested,
                "age_quarters": 0, "source_status": "ok"}
    candidates = []
    parent = paths["normalized"].parent
    for path in parent.glob("*.json") if parent.exists() else []:
        if path.stem < requested and _read_json(path):
            candidates.append(path.stem)
    if candidates:
        source = max(candidates)
        return {"data": _read_json(snapshot_paths(data_dir, source)["normalized"]),
                "requested_quarter": requested, "source_quarter": source,
                "age_quarters": quarter_distance(requested, source), "source_status": "fallback"}
    return {"data": None, "requested_quarter": requested, "source_quarter": None,
            "age_quarters": None, "source_status": "failed"}


def update_manifest(quarter: str, checkpoint: dict[str, Any], *, data_dir: str | Path | None = None) -> dict[str, Any]:
    paths = snapshot_paths(data_dir, quarter)
    manifest = _read_json(paths["manifest"]) or {"quarterly_sales_baseline": {}}
    manifest.setdefault("quarterly_sales_baseline", {})[validate_quarter(quarter)] = {
        key: checkpoint.get(key) for key in (
            "status", "source_status", "source_quarter", "api_total_count", "raw_row_count", "duplicate_grain_count",
            "api_call_count", "completed_pages", "failed_pages", "started_at", "completed_at")
    }
    if checkpoint.get("status") == "complete":
        manifest["latest_successful_quarter"] = validate_quarter(quarter)
    _write_json(paths["manifest"], manifest)
    return manifest
