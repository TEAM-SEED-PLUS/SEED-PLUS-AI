"""Same-quarter OA-22176/OA-22173 exact-grain calibration artifacts."""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from commercial_store_snapshot import percentile_ranks
from consumption_data_probe import ConsumptionDataError, SeoulConsumptionClient
from quarterly_sales_baseline import DEFAULT_DATA_DIR, grain_key, label_to_api_quarter, snapshot_paths, validate_quarter


SEOUL_TZ = ZoneInfo("Asia/Seoul")
DENOMINATOR_SOURCE = "seoul_commercial_analysis_OA22173"
COMPETITION_STORE_SOURCE = "small_business_commercial_store_api"


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


def store_paths(data_dir: str | Path | None, quarter: str) -> dict[str, Path]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    q = validate_quarter(quarter)
    return {
        "raw": root / "baseline" / "oa22173_raw" / f"{q}.jsonl",
        "checkpoint": root / "baseline" / "oa22173_checkpoints" / f"{q}.json",
        "aligned": root / "baseline" / "aligned" / f"{q}.json",
        "normalized": root / "baseline" / "aligned_normalized" / f"{q}.json",
    }


def _retry(operation: Callable[[], dict[str, Any]], retries: int, backoff: float,
           sleep: Callable[[float], None]) -> dict[str, Any]:
    last = None
    for attempt in range(retries + 1):
        try:
            return operation()
        except ConsumptionDataError as exc:
            last = exc
            if attempt < retries:
                sleep(backoff * (2 ** attempt))
    raise last or ConsumptionDataError("unknown OA-22173 failure")


def collect_oa22173(client: SeoulConsumptionClient, quarter: str, *, data_dir: str | Path | None = None,
                    page_size: int = 1000, retries: int = 2, backoff_seconds: float = 0.5,
                    sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    q, api_quarter = validate_quarter(quarter), label_to_api_quarter(quarter)
    paths = store_paths(data_dir, q)
    checkpoint = _read_json(paths["checkpoint"]) or {
        "requested_quarter": q, "source_quarter": q, "completed_pages": [],
        "api_call_count": 0, "started_at": _now(),
    }
    completed = {int(x) for x in checkpoint.get("completed_pages", [])}
    total = checkpoint.get("api_total_count")
    page_count = max(1, (int(total) + page_size - 1) // page_size) if total is not None else 1
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    duplicates = 0
    if paths["raw"].is_file():
        for line in paths["raw"].read_text(encoding="utf-8").splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if isinstance(row, dict): records[grain_key(row)] = row
    failed = set()
    page = 1
    while page <= page_count:
        if page in completed:
            page += 1; continue
        attempts = 0
        def request() -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            start = (page - 1) * page_size + 1
            return client.district_stores_page(start, start + page_size - 1)
        try:
            result = _retry(request, retries, backoff_seconds, sleep)
        except ConsumptionDataError as exc:
            checkpoint["api_call_count"] += attempts; checkpoint["last_error"] = str(exc)
            failed.add(page); page += 1; continue
        checkpoint["api_call_count"] += attempts
        if total is None:
            total = int(result.get("total_count") or 0); checkpoint["api_total_count"] = total
            page_count = max(1, (total + page_size - 1) // page_size)
        for row in result.get("items", []):
            if str(row.get("STDR_YYQU_CD") or "") != api_quarter: continue
            key = grain_key(row)
            if key in records: duplicates += 1
            else: records[key] = row
        completed.add(page); checkpoint["completed_pages"] = sorted(completed)
        checkpoint["updated_at"] = _now(); _write_json(paths["checkpoint"], checkpoint)
        page += 1
    missing = sorted(set(range(1, page_count + 1)) - completed)
    paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    temporary = paths["raw"].with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as out:
        for key in sorted(records):
            out.write(json.dumps(records[key], ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(paths["raw"])
    source_status = "partial" if missing and records else "failed" if missing else "ok"
    checkpoint.update({"status": "complete" if not missing else source_status,
                       "source_status": source_status, "failed_pages": missing,
                       "raw_row_count": len(records), "duplicate_grain_count": duplicates,
                       "completed_at": _now() if not missing else None})
    _write_json(paths["checkpoint"], checkpoint)
    return checkpoint


def _number(row: dict[str, Any], key: str) -> float | None:
    try: return float(row[key]) if row.get(key) not in (None, "") else None
    except (TypeError, ValueError): return None


def exact_join(quarter: str, *, data_dir: str | Path | None = None,
               external_snapshot_path: str | Path | None = None) -> dict[str, Any]:
    q = validate_quarter(quarter)
    sales_path = snapshot_paths(data_dir, q)["raw"]
    stores_path = store_paths(data_dir, q)["raw"]
    def load_jsonl(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
        output = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if isinstance(row, dict): output.setdefault(grain_key(row), row)
        return output
    sales, stores = load_jsonl(sales_path), load_jsonl(stores_path)
    sales_keys, store_keys = set(sales), set(stores)
    matched = sales_keys & store_keys
    sales_only, store_only = sales_keys - store_keys, store_keys - sales_keys
    external_path = Path(external_snapshot_path) if external_snapshot_path else (
        Path(__file__).resolve().parent / "data" / "commercial_stores" / "aggregated" / "2026Q3.json")
    external = _read_json(external_path) or {}
    external_districts = external.get("districts") or {}
    by_district: dict[str, dict[str, Any]] = {}
    joined_rows = []
    for key in sorted(matched):
        sale, store = sales[key], stores[key]
        district = str(sale.get("SIGNGU_CD_NM") or "")
        if district and not district.endswith("구"): district += "구"
        amount = _number(sale, "THSMON_SELNG_AMT") or 0.0
        transactions = _number(sale, "THSMON_SELNG_CO") or 0.0
        count = _number(store, "SIMILR_INDUTY_STOR_CO")
        per_store = amount / count if isinstance(count, (int, float)) and count > 0 else None
        item = {"grain": {"quarter": key[0], "district_code": key[1], "service_industry_code": key[2]},
                "district": district, "service_industry_name": sale.get("SVC_INDUTY_CD_NM"),
                "estimated_sales": amount, "estimated_transactions": transactions,
                "similar_industry_store_count": count, "sales_per_store_industry": per_store,
                "source_status": "ok" if per_store is not None else "no_data"}
        joined_rows.append(item)
        block = by_district.setdefault(district, {"district": district, "district_total_sales": 0.0,
            "district_total_transactions": 0.0, "district_total_matched_stores": 0.0,
            "matched_industry_count": 0, "valid_industry_per_store_count": 0,
            "industry_sales_per_store": [], "weighted_numerator": 0.0, "weighted_denominator": 0.0})
        block["district_total_sales"] += amount; block["district_total_transactions"] += transactions
        block["matched_industry_count"] += 1
        if count is not None: block["district_total_matched_stores"] += count
        if per_store is not None:
            block["valid_industry_per_store_count"] += 1
            block["industry_sales_per_store"].append(per_store)
            block["weighted_numerator"] += per_store * amount
            block["weighted_denominator"] += amount
    for district, block in by_district.items():
        sales_total, stores_total = block["district_total_sales"], block["district_total_matched_stores"]
        values = block.pop("industry_sales_per_store")
        numerator, denominator = block.pop("weighted_numerator"), block.pop("weighted_denominator")
        block.update({
            "sales_per_matched_store": sales_total / stores_total if stores_total > 0 else None,
            "sales_weighted_industry_sales_per_store": numerator / denominator if denominator > 0 else None,
            "median_industry_sales_per_store": statistics.median(values) if values else None,
            "external_store_count_diagnostics_only": (external_districts.get(district) or {}).get("total_store_count"),
            "denominator_source": DENOMINATOR_SOURCE,
            "competition_store_source": COMPETITION_STORE_SOURCE,
        })
    mismatch_codes = {key[2] for key in sales_only} | {key[2] for key in store_only}
    diagnostics = {"sales_row_count": len(sales), "store_row_count": len(stores),
                   "matched_row_count": len(matched), "sales_only_count": len(sales_only),
                   "store_only_count": len(store_only), "industry_code_mismatch_count": len(mismatch_codes),
                   "match_rate_sales": round(len(matched) / len(sales), 6) if sales else None,
                   "sales_only_grains": [list(x) for x in sorted(sales_only)],
                   "store_only_grains": [list(x) for x in sorted(store_only)]}
    output = {"snapshot_quarter": q, "join_key": ["STDR_YYQU_CD", "SIGNGU_CD", "SVC_INDUTY_CD"],
              "denominator_source": DENOMINATOR_SOURCE, "production_scoring_connected": False,
              "diagnostics": diagnostics, "districts": dict(sorted(by_district.items())), "joined_rows": joined_rows}
    _write_json(store_paths(data_dir, q)["aligned"], output)
    return output


def build_aligned_preview(quarter: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    q = validate_quarter(quarter); aligned = _read_json(store_paths(data_dir, q)["aligned"])
    if not aligned: raise FileNotFoundError("aligned snapshot is missing")
    districts = aligned.get("districts") or {}
    specs = {"candidate_a_sales_per_matched_store": "sales_per_matched_store",
             "candidate_b_sales_weighted_industry_mean": "sales_weighted_industry_sales_per_store",
             "candidate_c_industry_median": "median_industry_sales_per_store"}
    for name, field in specs.items():
        pcts = percentile_ranks({d: row.get(field) for d, row in districts.items()})
        valid = sorted(((d, row.get(field)) for d, row in districts.items() if isinstance(row.get(field), (int,float))),
                       key=lambda x: (-x[1], x[0]))
        ranks = {d: i for i, (d, _) in enumerate(valid, 1)}
        for district, row in districts.items():
            row[f"{name}_percentile"] = pcts[district]; row[f"{name}_rank"] = ranks.get(district)
    output = {"snapshot_quarter": q, "hybrid_status": "preview_only", "production_scoring_connected": False,
              "denominator_source": DENOMINATOR_SOURCE, "candidate_metrics": specs,
              "diagnostics": aligned.get("diagnostics"), "districts": districts}
    _write_json(store_paths(data_dir, q)["normalized"], output)
    return output
