"""월간 관광 기저 수집, fallback, 정규화 및 파일 snapshot 계층."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from common import TOURISM_DATA_SEOUL_AREA_CODE, TOURISM_DATA_SEOUL_SIGNGU, normalize_district
    from tourism_baseline_api import (
        INDICATOR_FIELD_SPECS,
        OPERATION_SPECS_BY_METRIC,
        TOURISM_INDICATOR_CODES,
        TourismBaselineClient,
        _validate_base_ym,
    )
    from v1_config import MAX_TOURISM_FALLBACK_MONTHS
except ImportError:  # pragma: no cover
    from .common import TOURISM_DATA_SEOUL_AREA_CODE, TOURISM_DATA_SEOUL_SIGNGU, normalize_district
    from .tourism_baseline_api import (
        INDICATOR_FIELD_SPECS,
        OPERATION_SPECS_BY_METRIC,
        TOURISM_INDICATOR_CODES,
        TourismBaselineClient,
        _validate_base_ym,
    )
    from .v1_config import MAX_TOURISM_FALLBACK_MONTHS


DEFAULT_TOURISM_DATA_DIR = Path(__file__).resolve().parent / "data" / "tourism"
EXPECTED_INDICATOR_COUNT = sum(len(codes) for codes in TOURISM_INDICATOR_CODES.values())
EXPECTED_DISTRICT_COUNT = len(TOURISM_DATA_SEOUL_SIGNGU)


def _paths(data_dir: str | Path | None, base_ym: str) -> dict[str, Path]:
    root = Path(data_dir) if data_dir is not None else DEFAULT_TOURISM_DATA_DIR
    return {
        "root": root,
        "raw": root / "raw" / f"{base_ym}.json",
        "normalized": root / "normalized" / f"{base_ym}.json",
        "manifest": root / "manifest.json",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_tourism_indicator_rows(
    metric_group: str,
    indicator_code: str,
    requested_month: str,
    api_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """API별 row를 공통 구조로 변환하고 서울 25개구 완전성을 보장한다."""
    code_field, name_field, value_field = INDICATOR_FIELD_SPECS[metric_group]
    code_to_district = {code: district for district, code in TOURISM_DATA_SEOUL_SIGNGU.items()}
    accepted: dict[str, dict[str, Any]] = {}
    excluded_aggregate_rows = 0
    unknown_codes: set[str] = set()
    duplicate_district_codes: set[str] = set()

    for item in api_result.get("items", []):
        if not isinstance(item, dict):
            continue
        signgu_cd = str(item.get("signguCd", "")).strip()
        if signgu_cd == "0":
            excluded_aggregate_rows += 1
            continue
        if signgu_cd not in code_to_district:
            if signgu_cd:
                unknown_codes.add(signgu_cd)
            continue
        if signgu_cd in accepted:
            duplicate_district_codes.add(signgu_cd)
            continue
        raw_value = _safe_float(item.get(value_field))
        source_month = str(item.get("baseYm") or requested_month)
        status = "ok" if raw_value is not None else "no_data"
        accepted[signgu_cd] = {
            "district": code_to_district[signgu_cd],
            "signgu_cd": signgu_cd,
            "metric_group": metric_group,
            "indicator_code": str(item.get(code_field) or indicator_code),
            "indicator_name": str(item.get(name_field) or ""),
            "raw_value": raw_value,
            "requested_month": requested_month,
            "source_month": source_month,
            "source_status": status,
        }

    api_status = str((api_result.get("source_status") or {}).get("status") or "failed")
    missing_status = "failed" if api_status == "failed" else "no_data"
    failure_reason = str((api_result.get("source_status") or {}).get("reason") or "")
    rows: list[dict[str, Any]] = []
    for district, signgu_cd in TOURISM_DATA_SEOUL_SIGNGU.items():
        row = accepted.get(signgu_cd)
        if row is None:
            row = {
                "district": district,
                "signgu_cd": signgu_cd,
                "metric_group": metric_group,
                "indicator_code": indicator_code,
                "indicator_name": "",
                "raw_value": None,
                "requested_month": requested_month,
                "source_month": requested_month,
                "source_status": missing_status,
            }
            if failure_reason:
                row["failure_reason"] = failure_reason
        rows.append(row)

    diagnostics = {
        "api_status": api_status,
        "district_count": len(accepted),
        "excluded_aggregate_rows": excluded_aggregate_rows,
        "unknown_district_codes": sorted(unknown_codes),
        "duplicate_district_codes": sorted(duplicate_district_codes),
        "missing_district_codes": sorted(set(code_to_district) - set(accepted)),
        "api_call_count": int(api_result.get("api_call_count", 1) or 1),
        "page_count": int(api_result.get("page_count", 1) or 1),
        "total_count": api_result.get("total_count"),
    }
    return rows, diagnostics


def _previous_raw_snapshots(data_dir: str | Path | None, requested_month: str) -> list[dict[str, Any]]:
    raw_dir = _paths(data_dir, requested_month)["raw"].parent
    snapshots = []
    if not raw_dir.exists():
        return snapshots
    for path in sorted(raw_dir.glob("*.json"), reverse=True):
        if path.stem >= requested_month:
            continue
        payload = _read_json(path)
        if payload:
            snapshots.append(payload)
    return snapshots


def apply_cached_fallbacks(
    rows: list[dict[str, Any]],
    requested_month: str,
    *,
    data_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    previous = _previous_raw_snapshots(data_dir, requested_month)
    indices: list[dict[tuple[str, str], dict[str, Any]]] = []
    for snapshot in previous:
        index = {}
        for row in snapshot.get("rows", []):
            if (
                isinstance(row, dict)
                and row.get("raw_value") is not None
                and row.get("source_status") in {"ok", "fallback"}
            ):
                index[(str(row.get("signgu_cd")), str(row.get("indicator_code")))] = row
        indices.append(index)

    output = []
    for current in rows:
        row = dict(current)
        if row.get("source_status") not in {"no_data", "failed"}:
            output.append(row)
            continue
        previous_row = next(
            (
                index.get((str(row.get("signgu_cd")), str(row.get("indicator_code"))))
                for index in indices
                if index.get((str(row.get("signgu_cd")), str(row.get("indicator_code")))) is not None
            ),
            None,
        )
        if previous_row is None:
            output.append(row)
            continue
        source_month = str(previous_row.get("source_month") or previous_row.get("requested_month") or "")
        age_months = _month_distance(requested_month, source_month)
        if age_months is None or age_months < 0 or age_months > MAX_TOURISM_FALLBACK_MONTHS:
            row.update({
                "source_month": source_month or None,
                "age_months": age_months,
                "fallback_reason": "fallback_age_exceeded",
            })
            output.append(row)
            continue
        original_status = str(row.get("source_status"))
        row.update({
            "indicator_name": previous_row.get("indicator_name", row.get("indicator_name", "")),
            "raw_value": previous_row.get("raw_value"),
            "source_month": source_month,
            "source_status": "fallback",
            "fallback_reason": (
                "current_month_no_data" if original_status == "no_data" else "current_month_api_failed"
            ),
        })
        row["age_months"] = age_months
        row.pop("failure_reason", None)
        output.append(row)
    return output


def collect_tourism_month(
    base_ym: str,
    service_key: str | None = None,
    *,
    data_dir: str | Path | None = None,
    client: TourismBaselineClient | None = None,
    num_of_rows: int = 1000,
) -> dict[str, Any]:
    requested_month = _validate_base_ym(base_ym)
    api = client or TourismBaselineClient(service_key)
    rows: list[dict[str, Any]] = []
    indicator_status: dict[str, dict[str, Any]] = {}
    api_call_count = 0
    excluded_aggregate_rows = 0
    unknown_codes: set[str] = set()

    for metric_group, indicator_codes in TOURISM_INDICATOR_CODES.items():
        operation = OPERATION_SPECS_BY_METRIC[metric_group]
        for indicator_code in indicator_codes:
            result = api.request_all_pages(
                operation,
                base_ym=requested_month,
                area_cd=TOURISM_DATA_SEOUL_AREA_CODE,
                signgu_cd=None,
                indicator_code=indicator_code,
                num_of_rows=num_of_rows,
            )
            extracted, diagnostics = extract_tourism_indicator_rows(
                metric_group, indicator_code, requested_month, result
            )
            rows.extend(extracted)
            indicator_status[indicator_code] = {
                "metric_group": metric_group,
                "operation": operation,
                **diagnostics,
            }
            api_call_count += diagnostics["api_call_count"]
            excluded_aggregate_rows += diagnostics["excluded_aggregate_rows"]
            unknown_codes.update(diagnostics["unknown_district_codes"])

    rows = apply_cached_fallbacks(rows, requested_month, data_dir=data_dir)
    snapshot = {
        "requested_month": requested_month,
        "collection_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "expected_indicator_count": EXPECTED_INDICATOR_COUNT,
        "expected_district_count": EXPECTED_DISTRICT_COUNT,
        "api_call_count": api_call_count,
        "indicator_codes": TOURISM_INDICATOR_CODES,
        "indicator_status": indicator_status,
        "diagnostics": {
            "excluded_aggregate_rows": excluded_aggregate_rows,
            "unknown_district_codes": sorted(unknown_codes),
        },
        "rows": rows,
    }
    _write_json(_paths(data_dir, requested_month)["raw"], snapshot)
    return snapshot


def normalize_tourism_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("indicator_code")), []).append(row)

    normalized_rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for indicator_code, group in grouped.items():
        valid = [
            float(row["raw_value"])
            for row in group
            if row.get("source_status") in {"ok", "fallback"} and row.get("raw_value") is not None
        ]
        min_value = min(valid) if valid else None
        max_value = max(valid) if valid else None
        reason = "all_values_equal" if valid and min_value == max_value else ""
        diagnostics[indicator_code] = {
            "valid_value_count": len(valid),
            "min_value": min_value,
            "max_value": max_value,
        }
        if reason:
            diagnostics[indicator_code]["normalization_reason"] = reason

        for source in group:
            row = dict(source)
            if row.get("source_status") not in {"ok", "fallback"} or row.get("raw_value") is None:
                row["normalized_value"] = None
            elif min_value == max_value:
                row["normalized_value"] = 50.0
            else:
                value = (float(row["raw_value"]) - min_value) / (max_value - min_value) * 100
                row["normalized_value"] = round(max(0.0, min(100.0, value)), 2)
            normalized_rows.append(row)
    return normalized_rows, diagnostics


def _district_tree(rows: list[dict[str, Any]]) -> dict[str, Any]:
    districts: dict[str, Any] = {}
    for row in rows:
        district = str(row["district"])
        metric_group = str(row["metric_group"])
        indicator_code = str(row["indicator_code"])
        district_block = districts.setdefault(district, {
            "signgu_cd": row["signgu_cd"],
            "metrics": {},
        })
        district_block["metrics"].setdefault(metric_group, {})[indicator_code] = {
            key: row.get(key) for key in (
                "indicator_name", "raw_value", "normalized_value", "requested_month",
                "source_month", "source_status", "fallback_reason",
            ) if row.get(key) is not None
        }
    return districts


def _snapshot_status(counts: dict[str, int]) -> str:
    valid = counts.get("ok", 0) + counts.get("fallback", 0)
    missing = counts.get("no_data", 0) + counts.get("failed", 0)
    if missing == 0:
        return "complete"
    if valid == 0:
        return "failed"
    return "partial"


def _save_manifest(data_dir: str | Path | None, base_ym: str, manifest_entry: dict[str, Any]) -> None:
    path = _paths(data_dir, base_ym)["manifest"]
    manifest = _read_json(path) or {"snapshots": {}}
    snapshots = manifest.setdefault("snapshots", {})
    snapshots[base_ym] = manifest_entry
    _write_json(path, manifest)


def normalize_tourism_month(
    base_ym: str,
    *,
    data_dir: str | Path | None = None,
    raw_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_month = _validate_base_ym(base_ym)
    raw = raw_snapshot or _read_json(_paths(data_dir, requested_month)["raw"])
    if raw is None:
        raise FileNotFoundError(f"raw 관광 snapshot이 없습니다: {requested_month}")
    rows, normalization = normalize_tourism_rows(raw.get("rows", []))
    counts = {status: sum(1 for row in rows if row.get("source_status") == status) for status in (
        "ok", "fallback", "no_data", "failed"
    )}
    status = _snapshot_status(counts)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot = {
        "requested_month": requested_month,
        "generated_at": generated_at,
        "snapshot_status": status,
        "districts": _district_tree(rows),
        "rows": rows,
        "diagnostics": {
            **raw.get("diagnostics", {}),
            "normalization": normalization,
        },
    }
    _write_json(_paths(data_dir, requested_month)["normalized"], snapshot)
    manifest_entry = {
        "requested_month": requested_month,
        "generated_at": generated_at,
        "snapshot_status": status,
        "expected_indicator_count": EXPECTED_INDICATOR_COUNT,
        "expected_district_count": EXPECTED_DISTRICT_COUNT,
        "api_call_count": int(raw.get("api_call_count", 0) or 0),
        "success_value_count": counts["ok"],
        "fallback_value_count": counts["fallback"],
        "no_data_value_count": counts["no_data"],
        "failed_value_count": counts["failed"],
        "excluded_aggregate_rows": int((raw.get("diagnostics") or {}).get("excluded_aggregate_rows", 0) or 0),
        "unknown_district_codes": (raw.get("diagnostics") or {}).get("unknown_district_codes", []),
    }
    _save_manifest(data_dir, requested_month, manifest_entry)
    return snapshot


def refresh_tourism_month(
    base_ym: str,
    service_key: str | None = None,
    *,
    data_dir: str | Path | None = None,
    client: TourismBaselineClient | None = None,
    num_of_rows: int = 1000,
) -> dict[str, Any]:
    raw = collect_tourism_month(
        base_ym, service_key, data_dir=data_dir, client=client, num_of_rows=num_of_rows
    )
    return normalize_tourism_month(base_ym, data_dir=data_dir, raw_snapshot=raw)


def load_tourism_snapshot(
    base_ym: str,
    service_key: str | None = None,
    *,
    data_dir: str | Path | None = None,
    refresh: bool = False,
    client: TourismBaselineClient | None = None,
) -> dict[str, Any]:
    requested_month = _validate_base_ym(base_ym)
    cached = _read_json(_paths(data_dir, requested_month)["normalized"])
    if cached is not None and not refresh:
        return cached
    return refresh_tourism_month(
        requested_month, service_key, data_dir=data_dir, client=client
    )


def load_cached_tourism_snapshot(
    base_ym: str,
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """외부 API 호출 없이 요청월 또는 가장 최근 이전 성공 snapshot을 읽는다."""
    requested_month = _validate_base_ym(base_ym)
    exact = _read_json(_paths(data_dir, requested_month)["normalized"])
    if exact is not None and exact.get("snapshot_status") in {"complete", "partial"}:
        snapshot = deepcopy(exact)
        snapshot["source_month"] = str(snapshot.get("requested_month") or requested_month)
        snapshot["source_status"] = "ok" if snapshot.get("snapshot_status") == "complete" else "partial"
        snapshot["age_months"] = 0
        return snapshot

    normalized_dir = _paths(data_dir, requested_month)["normalized"].parent
    candidates = sorted(
        (path for path in normalized_dir.glob("*.json") if path.stem.isdigit() and path.stem < requested_month),
        key=lambda path: path.stem,
        reverse=True,
    )
    for path in candidates:
        cached = _read_json(path)
        if cached is None or cached.get("snapshot_status") not in {"complete", "partial"}:
            continue
        snapshot = deepcopy(cached)
        source_month = str(snapshot.get("requested_month") or path.stem)
        age_months = _month_distance(requested_month, source_month)
        snapshot.update({
            "requested_month": requested_month,
            "source_month": source_month,
            "source_status": "fallback",
            "fallback_reason": "requested_month_snapshot_missing",
            "age_months": age_months,
        })
        if age_months is None or age_months < 0 or age_months > MAX_TOURISM_FALLBACK_MONTHS:
            snapshot["fallback_reason"] = "fallback_age_exceeded"
        return snapshot
    return None


def _month_distance(requested_month: str, source_month: str) -> int | None:
    try:
        requested_year, requested_value = int(requested_month[:4]), int(requested_month[4:6])
        source_year, source_value = int(source_month[:4]), int(source_month[4:6])
    except (TypeError, ValueError):
        return None
    return (requested_year - source_year) * 12 + requested_value - source_value


def get_cached_district_tourism_baseline(
    district: str,
    base_ym: str,
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    district_ko, _ = normalize_district(district)
    snapshot = load_cached_tourism_snapshot(base_ym, data_dir=data_dir)
    if snapshot is None:
        return None
    district_data = snapshot.get("districts", {}).get(district_ko)
    if not isinstance(district_data, dict):
        return None
    district_data = deepcopy(district_data)
    if snapshot.get("source_status") == "fallback":
        for records in (district_data.get("metrics") or {}).values():
            if not isinstance(records, dict):
                continue
            for record in records.values():
                if not isinstance(record, dict) or record.get("normalized_value") is None:
                    continue
                record["requested_month"] = snapshot["requested_month"]
                record["source_month"] = snapshot["source_month"]
                record["source_status"] = "fallback"
                record["fallback_reason"] = snapshot["fallback_reason"]
                record["age_months"] = snapshot["age_months"]
    return {
        "requested_month": snapshot["requested_month"],
        "source_month": snapshot.get("source_month", snapshot["requested_month"]),
        "source_status": snapshot.get("source_status", "ok"),
        "fallback_reason": snapshot.get("fallback_reason"),
        "age_months": snapshot.get("age_months", 0),
        "snapshot_status": snapshot["snapshot_status"],
        "district": district_ko,
        **district_data,
    }


def get_district_tourism_baseline(
    district: str,
    base_ym: str,
    service_key: str | None = None,
    *,
    data_dir: str | Path | None = None,
    refresh: bool = False,
    client: TourismBaselineClient | None = None,
) -> dict[str, Any]:
    district_ko, _ = normalize_district(district)
    snapshot = load_tourism_snapshot(
        base_ym, service_key, data_dir=data_dir, refresh=refresh, client=client
    )
    return {
        "requested_month": snapshot["requested_month"],
        "snapshot_status": snapshot["snapshot_status"],
        "district": district_ko,
        **snapshot.get("districts", {}).get(district_ko, {}),
    }
