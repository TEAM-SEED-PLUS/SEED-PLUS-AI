"""Production adapter for the measured quarterly + realtime consumption budget."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from commercial_store_snapshot import percentile_ranks
from common import DEFAULT_SEOUL_KEY
from consumption_data_probe import ConsumptionDataError, SeoulConsumptionClient
from realtime_commerce_preview import save_realtime
from sales_store_alignment import store_paths
from quarterly_sales_baseline import DEFAULT_DATA_DIR, quarter_distance, validate_quarter
from v1_config import REALTIME_COMMERCE_SPENDING_CAP, SALES_BASELINE_SPENDING_CAP


SEOUL_TZ = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent
DEFAULT_REFERENCE = DEFAULT_DATA_DIR / "realtime" / "reference" / "places_20260823.json"
DEFAULT_MAPPING = DEFAULT_DATA_DIR / "realtime" / "reference" / "mappings_20260823.json"


def _load(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def query_quarter(date_value: str) -> str:
    digits = str(date_value).replace("-", "")
    if len(digits) < 6 or not digits[:6].isdigit():
        raise ValueError("query date must contain YYYYMMDD")
    return f"{digits[:4]}Q{(int(digits[4:6]) - 1) // 3 + 1}"


def sales_baseline_bonus(percentile: Any) -> float:
    value = float(percentile) if isinstance(percentile, (int, float)) else 0.0
    return round(max(0.0, min(SALES_BASELINE_SPENDING_CAP, value / 100 * SALES_BASELINE_SPENDING_CAP)), 4)


def realtime_commerce_bonus(percentile: Any) -> float:
    value = float(percentile) if isinstance(percentile, (int, float)) else 0.0
    return round(max(0.0, min(REALTIME_COMMERCE_SPENDING_CAP,
                              value / 100 * REALTIME_COMMERCE_SPENDING_CAP)), 4)


def load_sales_baseline(district: str, requested_quarter: str, *,
                        data_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    requested = validate_quarter(requested_quarter)
    parent = root / "baseline" / "aligned_normalized"
    candidates = []
    for path in parent.glob("*.json") if parent.exists() else []:
        if path.stem <= requested:
            value = _load(path)
            checkpoint = _load(store_paths(root, path.stem)["checkpoint"]) or {}
            if value and checkpoint.get("source_status") == "ok" and len(value.get("districts") or {}) == 25:
                candidates.append(path.stem)
    if not candidates:
        return {"source": "OA-22176+OA-22173", "requested_quarter": requested,
                "source_quarter": None, "age_quarters": None, "source_status": "failed",
                "sales_per_matched_store": None, "percentile": None, "bonus": 0.0}
    source = max(candidates); snapshot = _load(parent / f"{source}.json") or {}
    row = (snapshot.get("districts") or {}).get(district) or {}
    percentile = row.get("candidate_a_sales_per_matched_store_percentile")
    status = "ok" if source == requested else "fallback"
    if not isinstance(percentile, (int, float)):
        status = "no_data"
    return {"source": "OA-22176+OA-22173", "denominator_source": "seoul_commercial_analysis_OA22173",
            "requested_quarter": requested, "source_quarter": source,
            "age_quarters": quarter_distance(requested, source), "source_status": status,
            "sales_per_matched_store": row.get("sales_per_matched_store"), "percentile": percentile,
            "bonus": sales_baseline_bonus(percentile),
            "semantics": "서울시 추정매출과 동일 업종 모집단 점포수를 이용한 자치구 상대 소비 수준"}


def _parse_commerce_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    for form in ("%Y%m%d %H%M", "%Y-%m-%d %H:%M"):
        try: return datetime.strptime(text, form).replace(tzinfo=SEOUL_TZ)
        except ValueError: pass
    return None


def _time_band(hour: int) -> str:
    return "심야" if hour < 6 else "아침" if hour < 12 else "점심" if hour < 17 else "오후" if hour < 20 else "저녁"


def load_realtime_commerce(district: str, query_date: str, time_band: str, *,
                           data_dir: str | Path | None = None,
                           now: datetime | None = None) -> dict[str, Any]:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    latest_dir = root / "realtime" / "latest"
    snapshots = [_load(path) for path in latest_dir.glob("*.json")] if latest_dir.exists() else []
    snapshots = [x for x in snapshots if x and x.get("source_status") == "ok" and
                 isinstance(x.get("payment_amount_midpoint"), (int, float))]
    times = Counter(str(x.get("source_timestamp") or "") for x in snapshots if x.get("source_timestamp"))
    commerce_time = max(times, key=lambda value: (_parse_commerce_time(value) or datetime.min.replace(tzinfo=SEOUL_TZ),
                                                  times[value])) if times else None
    batch = [x for x in snapshots if x.get("source_timestamp") == commerce_time]
    manifest = _load(root / "realtime" / "manifest.json") or {}
    manifest_matches = not manifest or (
        manifest.get("source_status") == "ok"
        and manifest.get("latest_commerce_time") == commerce_time
        and int(manifest.get("valid_place_count") or 0) == int(manifest.get("place_count") or -1)
        and len(batch) == int(manifest.get("valid_place_count") or -1)
    )
    parsed = _parse_commerce_time(commerce_time)
    query_digits = str(query_date).replace("-", "")[:8]
    eligible = bool(manifest_matches and parsed and parsed.strftime("%Y%m%d") == query_digits and
                    _time_band(parsed.hour) == time_band and time_band != "심야")
    received_values = [datetime.fromisoformat(str(x["received_at"])) for x in batch if x.get("received_at")]
    latest_received = max(received_values) if received_values else None
    current = now or datetime.now(SEOUL_TZ)
    age = max(0.0, (current - parsed).total_seconds() / 60) if parsed else None
    if not eligible:
        reason = "night_policy" if time_band == "심야" else (
            "incomplete_batch" if not manifest_matches else "different_date_or_time_band")
        return {"source": "OA-22385", "source_status": "no_data", "eligibility_reason": reason,
                "commerce_time": commerce_time, "latest_commerce_time": commerce_time,
                "received_at": latest_received.isoformat(timespec="seconds") if latest_received else None,
                "age_minutes": round(age, 2) if age is not None else None,
                "assigned_place_count": 0, "valid_place_count": 0,
                "payment_percentile": None, "bonus": 0.0}
    percentiles = percentile_ranks({str(x["area_cd"]): x["payment_amount_midpoint"] for x in batch})
    assigned = [x for x in batch if ((x.get("district_mapping") or {}).get("assigned_district") == district)]
    values = [percentiles.get(str(x.get("area_cd"))) for x in assigned]
    values = [float(x) for x in values if isinstance(x, (int, float))]
    mean = sum(values) / len(values) if values else None
    status = "ok" if mean is not None else "no_data"
    return {"source": "OA-22385", "source_status": status,
            "commerce_time": commerce_time, "latest_commerce_time": commerce_time,
            "received_at": latest_received.isoformat(timespec="seconds") if latest_received else None,
            "age_minutes": round(age, 2) if age is not None else None,
            "assigned_place_count": len(assigned), "valid_place_count": len(values),
            "payment_percentile": round(mean, 4) if mean is not None else None,
            "bonus": realtime_commerce_bonus(mean),
            "semantics": "서울 실시간 상권현황 카드 결제금액 범위를 이용한 상대적 실시간 소비 활성 수준"}


def build_consumption_hybrid_input(district: str, query_date: str, time_band: str, *,
                                   data_dir: str | Path | None = None,
                                   now: datetime | None = None) -> dict[str, Any]:
    baseline = load_sales_baseline(district, query_quarter(query_date), data_dir=data_dir)
    realtime = load_realtime_commerce(district, query_date, time_band, data_dir=data_dir, now=now)
    return {"baseline": baseline, "realtime": realtime, "source_status": {
        "baseline": baseline["source_status"], "realtime": realtime["source_status"]}}


def attach_consumption_hybrid(normalized_data: dict[str, Any], *,
                              data_dir: str | Path | None = None,
                              now: datetime | None = None) -> dict[str, Any]:
    query = normalized_data.get("query") or {}
    try:
        normalized_data["consumption_hybrid"] = build_consumption_hybrid_input(
            str(query.get("district") or ""), str(query.get("date") or ""),
            str(query.get("time_band") or ""), data_dir=data_dir, now=now)
    except (OSError, ValueError):
        normalized_data["consumption_hybrid"] = {"baseline": {"source_status": "failed"},
                                                  "realtime": {"source_status": "failed"}}
    return normalized_data


def collect_realtime_once(*, client: SeoulConsumptionClient | None = None,
                          data_dir: str | Path | None = None,
                          reference_path: str | Path = DEFAULT_REFERENCE,
                          mapping_path: str | Path = DEFAULT_MAPPING,
                          probe_area: str | None = None) -> dict[str, Any]:
    """Probe one place; collect all official commerce places only on new CMRCL_TIME."""
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    reference, mappings = _load(reference_path) or {}, _load(mapping_path) or {}
    places = reference.get("places") or []
    if not places: return {"status": "failed", "reason": "reference_missing", "batch_skipped": True}
    by_mapping = {str(x["area_cd"]): x for x in mappings.get("mappings", [])}
    code = probe_area or str(places[0]["area_cd"]); api = client or SeoulConsumptionClient(DEFAULT_SEOUL_KEY)
    try: probe = api.realtime_commerce(code)
    except ConsumptionDataError as exc: return {"status": "failed", "reason": str(exc), "batch_skipped": True}
    source_time = str((probe.get("commercial") or {}).get("CMRCL_TIME") or "")
    manifest_path = root / "realtime" / "manifest.json"; manifest = _load(manifest_path) or {}
    if source_time and manifest.get("latest_commerce_time") == source_time:
        return {"status": "unchanged", "commerce_time": source_time, "api_call_count": 1, "batch_skipped": True}
    received = datetime.now(SEOUL_TZ); results = [probe]; failed = 0
    for place in places:
        place_code = str(place["area_cd"])
        if place_code == code: continue
        try: results.append(api.realtime_commerce(place_code))
        except ConsumptionDataError: failed += 1
    for result in results:
        save_realtime(result, by_mapping.get(str(result.get("area_cd") or "")), data_dir=root, received_at=received)
    manifest = {"latest_commerce_time": source_time, "received_at": received.isoformat(timespec="seconds"),
                "place_count": len(places), "valid_place_count": sum(x.get("source_status") == "ok" for x in results),
                "failed_place_count": failed, "source_status": "ok" if not failed else "partial"}
    _write(manifest_path, manifest)
    return {"status": "collected", "commerce_time": source_time, "api_call_count": len(places), **manifest}


def monitoring_snapshot(query_date: str, time_band: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    rows = {}
    for district in ("강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
                     "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
                     "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구"):
        value = build_consumption_hybrid_input(district, query_date, time_band, data_dir=data_dir)
        baseline, realtime = value["baseline"], value["realtime"]
        rows[district] = {"source_quarter": baseline.get("source_quarter"), "age_quarters": baseline.get("age_quarters"),
                          "baseline_percentile": baseline.get("percentile"),
                          "realtime_percentile": realtime.get("payment_percentile"),
                          "hybrid_mode": "sales_baseline_plus_realtime" if realtime.get("source_status") == "ok" else "sales_baseline_only",
                          "legacy_oa_fallback": baseline.get("source_status") not in {"ok", "fallback"},
                          "latest_commerce_time": realtime.get("commerce_time"), "realtime_age_minutes": realtime.get("age_minutes")}
    manifest = _load((Path(data_dir) if data_dir else DEFAULT_DATA_DIR) / "realtime" / "manifest.json") or {}
    return {"query_date": query_date, "time_band": time_band, "failed_place_count": manifest.get("failed_place_count"),
            "districts": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="OA-22385 probe-based collector and consumption monitoring")
    parser.add_argument("--monitor-date"); parser.add_argument("--time-band", default="점심")
    args = parser.parse_args()
    value = monitoring_snapshot(args.monitor_date, args.time_band) if args.monitor_date else collect_realtime_once()
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
