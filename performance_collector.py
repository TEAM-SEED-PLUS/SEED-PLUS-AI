"""CLI KOPIS collector: enrich once, then serve district/time filters from JSON."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# CLI execution must behave like app.main: load local configuration before
# common.py captures environment-backed defaults.
load_dotenv()

from common import DEFAULT_KOPIS_KEY, KOPIS_DISTRICT_CODE_MAP, SEOUL_TZ, normalize_text
from performance_api import KOPISClient, extract_gu_from_address
from performance_cache import performance_snapshot_path
from snapshot_cache import write_snapshot


def _enrich(rows: list[dict[str, Any]], client: KOPISClient, collection_district: str = "",
            detail_cache: dict[str, dict[str, Any]] | None = None,
            facility_cache: dict[str, dict[str, Any]] | None = None,
            diagnostics: dict[str, int] | None = None) -> list[dict[str, Any]]:
    details = detail_cache if detail_cache is not None else {}
    facilities = facility_cache if facility_cache is not None else {}
    output = []
    stats = diagnostics if diagnostics is not None else {}
    for row in rows:
        performance_id = str(row.get("mt20id") or "")
        if performance_id and performance_id not in details:
            try:
                details[performance_id] = client.get_performance_detail(performance_id)
                stats["detail_success_count"] = stats.get("detail_success_count", 0) + 1
            except Exception as exc:
                details[performance_id] = {"error": type(exc).__name__}
                stats["detail_failure_count"] = stats.get("detail_failure_count", 0) + 1
        detail = details.get(performance_id) or {}
        facility_id = str(detail.get("mt10id") or "")
        if facility_id:
            stats["detail_with_mt10id_count"] = stats.get("detail_with_mt10id_count", 0) + 1
        if facility_id and facility_id not in facilities:
            try:
                facilities[facility_id] = client.get_facility_detail(facility_id)
                stats["facility_success_count"] = stats.get("facility_success_count", 0) + 1
            except Exception as exc:
                facilities[facility_id] = {"error": type(exc).__name__}
                stats["facility_failure_count"] = stats.get("facility_failure_count", 0) + 1
        facility = facilities.get(facility_id) or {}
        address = normalize_text(facility.get("adres"))
        district = extract_gu_from_address(address)
        if address:
            stats["address_count"] = stats.get("address_count", 0) + 1
        if district:
            stats["address_district_count"] = stats.get("address_district_count", 0) + 1
        if (district or collection_district) in KOPIS_DISTRICT_CODE_MAP:
            stats["seoul_district_mapped_count"] = stats.get("seoul_district_mapped_count", 0) + 1
        output.append({"source": "KOPIS", "mt20id": performance_id, "mt10id": facility_id,
                       "title": normalize_text(row.get("prfnm")),
                       "genre": normalize_text(row.get("genrenm") or detail.get("genrenm")),
                       "place": normalize_text(row.get("fcltynm") or facility.get("fcltynm")),
                       "address": address, "district_from_address": district,
                       "collection_district": collection_district,
                       "date_from": normalize_text(row.get("prfpdfrom")), "date_to": normalize_text(row.get("prfpdto")),
                       "schedule_text": normalize_text(detail.get("dtguidance")),
                       "runtime": normalize_text(detail.get("prfruntime")),
                       "price_text": normalize_text(detail.get("pcseguidance")),
                       "link_url": normalize_text(detail.get("relate")),
                       "openrun": normalize_text(row.get("openrun")), "poster": normalize_text(row.get("poster"))})
    return output


def collect_kopis_items(date_str: str, client: KOPISClient) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
    compact = date_str.replace("-", "")
    detail_cache: dict[str, dict[str, Any]] = {}
    facility_cache: dict[str, dict[str, Any]] = {}
    stats = {key: 0 for key in (
        "raw_list_count", "unique_mt20id_count", "detail_success_count", "detail_failure_count",
        "detail_with_mt10id_count", "facility_success_count", "facility_failure_count",
        "address_count", "address_district_count", "seoul_district_mapped_count",
        "collector_filter_before_count", "collector_filter_after_count", "final_item_count",
    )}
    try:
        rows = client.get_all_performances(compact, compact, signgucode="11", signgucodesub=None)
    except Exception:
        # Some KOPIS deployments reject the Seoul-wide form. District list calls
        # remain collector-only and share global detail/facility de-duplication.
        merged: dict[str, tuple[dict[str, Any], str]] = {}
        for district, codes in KOPIS_DISTRICT_CODE_MAP.items():
            rows = client.get_all_performances(compact, compact, signgucode=codes["signgucode"],
                                               signgucodesub=codes["signgucodesub"])
            for row in rows:
                key = str(row.get("mt20id") or "")
                if key and key not in merged:
                    merged[key] = (row, district)
        stats["raw_list_count"] = len(merged)
        stats["unique_mt20id_count"] = len(merged)
        stats["collector_filter_before_count"] = len(merged)
        output = []
        for row, district in merged.values():
            output.extend(_enrich([row], client, district, detail_cache, facility_cache, stats))
        stats["collector_filter_after_count"] = len(output)
        stats["final_item_count"] = len(output)
        return output, "district_fallback", stats
    stats["raw_list_count"] = len(rows)
    unique_rows = {str(row.get("mt20id") or f"missing:{index}"): row for index, row in enumerate(rows)}
    stats["unique_mt20id_count"] = len(unique_rows)
    stats["collector_filter_before_count"] = len(unique_rows)
    items = _enrich(list(unique_rows.values()), client, detail_cache=detail_cache,
                    facility_cache=facility_cache, diagnostics=stats)
    # Request-specific date/time-band filtering intentionally remains zero.
    stats["collector_filter_after_count"] = len(items)
    stats["final_item_count"] = len(items)
    return items, "seoul_wide", stats


def collect_performance_once(date_str: str | None = None, *, kopis_key: str = DEFAULT_KOPIS_KEY,
                             data_dir: str | Path | None = None, client: KOPISClient | None = None) -> dict[str, Any]:
    target = datetime.now(SEOUL_TZ).strftime("%Y-%m-%d") if not date_str else datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
    started = time.perf_counter()
    try:
        if client is None and not str(kopis_key).strip():
            raise RuntimeError("KOPIS_API_KEY is not configured")
        items, mode, diagnostics = collect_kopis_items(target, client or KOPISClient(kopis_key))
        mapped = diagnostics["seoul_district_mapped_count"]
        if not items:
            status = "empty"
        elif mapped == 0:
            status = "failed"
        elif (diagnostics["detail_failure_count"] or diagnostics["facility_failure_count"]
              or mapped < len(items)):
            status = "partial"
        else:
            status = "ok"
        payload = {"source_date": target,
                   "source_status": status, "collection_mode": mode,
                   "item_count": len(items), "diagnostics": diagnostics, "items": items}
    except Exception as exc:
        payload = {"source_date": target,
                   "source_status": "failed", "collection_mode": "failed", "item_count": 0,
                   "diagnostics": {}, "items": [], "error": type(exc).__name__}
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    completed = datetime.now(SEOUL_TZ).isoformat(timespec="seconds")
    payload["generated_at"] = completed
    payload["received_at"] = completed
    write_snapshot(performance_snapshot_path(target, data_dir), payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="KOPIS Seoul performance snapshot collector")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--kopis-key", default=DEFAULT_KOPIS_KEY)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--full", action="store_true", help="snapshot items도 stdout에 출력")
    args = parser.parse_args()
    result = collect_performance_once(args.date, kopis_key=args.kopis_key, data_dir=args.data_dir)
    if not args.full:
        result = {key: value for key, value in result.items() if key != "items"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
