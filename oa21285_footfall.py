"""Provider-aware OA percentile footfall adapter and S-DoT fallback selection."""

from __future__ import annotations

from typing import Any, Callable
from datetime import datetime

try:
    from common import SEOUL_TZ
    from oa21285_config import OA21285_FOOTFALL_ENABLED, OA21285_MIN_BAND_SNAPSHOTS
    from oa21285_history import summarize_district_percentile_history
    from v1_config import FOOTFALL_AVG_INFLOW_CAP, FOOTFALL_AVG_SPENDING_CAP, FOOTFALL_MAX_INFLOW_CAP
except ImportError:  # pragma: no cover
    from .common import SEOUL_TZ
    from .oa21285_config import OA21285_FOOTFALL_ENABLED, OA21285_MIN_BAND_SNAPSHOTS
    from .oa21285_history import summarize_district_percentile_history
    from .v1_config import FOOTFALL_AVG_INFLOW_CAP, FOOTFALL_AVG_SPENDING_CAP, FOOTFALL_MAX_INFLOW_CAP


def _bounded_percentile(value: Any) -> float | None:
    return max(0.0, min(100.0, float(value))) if isinstance(value, (int, float)) else None


def adapt_oa_history_to_footfall(summary: dict[str, Any], *, min_valid_snapshots: int = OA21285_MIN_BAND_SNAPSHOTS) -> dict[str, Any] | None:
    count = int(summary.get("snapshot_count") or 0)
    avg_percentile = _bounded_percentile(summary.get("population_percentile_avg_in_band"))
    max_percentile = _bounded_percentile(summary.get("population_percentile_max_in_band"))
    spike = summary.get("population_spike_ratio")
    if summary.get("source_status") != "ok" or count < min_valid_snapshots or avg_percentile is None or max_percentile is None or not isinstance(spike, (int, float)):
        return None
    inflow_level = min(FOOTFALL_AVG_INFLOW_CAP, avg_percentile / 100 * FOOTFALL_AVG_INFLOW_CAP)
    inflow_peak = min(FOOTFALL_MAX_INFLOW_CAP, max_percentile / 100 * FOOTFALL_MAX_INFLOW_CAP)
    spending_level = min(FOOTFALL_AVG_SPENDING_CAP, avg_percentile / 100 * FOOTFALL_AVG_SPENDING_CAP)
    latest = summary.get("latest_population_time")
    try:
        parsed_latest = datetime.fromisoformat(str(latest))
        now = datetime.now(SEOUL_TZ)
        if parsed_latest.tzinfo is None:
            parsed_latest = parsed_latest.replace(tzinfo=SEOUL_TZ)
        population_age_minutes = max(0, round((now - parsed_latest).total_seconds() / 60, 2))
    except (TypeError, ValueError):
        population_age_minutes = None
    return {
        "provider": "oa21285", "district": summary.get("district"), "time_band": summary.get("time_band"),
        "count": count, "items": [],
        "summary": {key: summary.get(key) for key in (
            "population_raw_current", "population_raw_avg_in_band", "population_raw_max_in_band",
            "population_raw_min_in_band", "population_percentile_avg_in_band",
            "population_percentile_max_in_band", "population_percentile_min_in_band",
            "population_spike_ratio", "source_timestamps",
        )},
        "contributions": {"inflow_level_bonus": round(inflow_level, 4),
            "inflow_peak_bonus": round(inflow_peak, 4), "spending_level_bonus": round(spending_level, 4)},
        "diagnostics": {"oa_temporal_spike_ratio": float(spike), "used_for_competition": False,
                        "reason": "semantic_scale_mismatch_with_sdot"},
        "source_status": {"status": "ok", "provider": "oa21285", "snapshot_count": count,
                          "normalization": "seoul_district_percentile", "fallback": False,
                          "latest_population_time": latest,
                          "latest_received_at": summary.get("latest_received_at"),
                          "population_age_minutes": population_age_minutes,
                          "first_population_time": summary.get("first_population_time"),
                          "last_population_time": summary.get("last_population_time")},
        "data_semantics": (
            "OA-21285 실시간 추정 인구 범위를 기반으로 한 동일 시점 서울 자치구 내 상대 위치(Percentile) 유동 수준이며, "
            "정확한 방문자 수를 뜻하지 않음"
        ),
    }


def select_footfall_provider(district: str, date: str, time_band: str, sdot_footfall: dict[str, Any], *,
                             history_dir=None, min_valid_snapshots: int = OA21285_MIN_BAND_SNAPSHOTS) -> dict[str, Any]:
    if OA21285_FOOTFALL_ENABLED and time_band != "심야":
        summary = summarize_district_percentile_history(district, date, time_band, history_dir=history_dir)
        adapted = adapt_oa_history_to_footfall(summary, min_valid_snapshots=min_valid_snapshots)
        if adapted is not None:
            return adapted
    fallback = dict(sdot_footfall or {})
    fallback.setdefault("provider", "sdot")
    status = dict(fallback.get("source_status") or {})
    status.setdefault("provider", "sdot")
    status.setdefault("fallback", bool(OA21285_FOOTFALL_ENABLED))
    fallback["source_status"] = status
    return fallback


def load_available_oa_footfall(district: str, date: str, time_band: str, *, history_dir=None,
                               min_valid_snapshots: int = OA21285_MIN_BAND_SNAPSHOTS) -> dict[str, Any] | None:
    """Read OA first for a lazy OA -> S-DoT provider chain; never uses OA at night."""
    if not OA21285_FOOTFALL_ENABLED or time_band == "심야":
        return None
    summary = summarize_district_percentile_history(district, date, time_band, history_dir=history_dir)
    return adapt_oa_history_to_footfall(summary, min_valid_snapshots=min_valid_snapshots)


def combine_indicator_footfall_sources(oa_footfall: dict[str, Any] | None,
                                       sdot_footfall: dict[str, Any]) -> dict[str, Any]:
    """Use OA for level signals when available and one S-DoT result for competition."""
    sdot = dict(sdot_footfall or {})
    sdot.setdefault("provider", "sdot")
    if oa_footfall is None:
        sdot["footfall_sources"] = {"inflow": "sdot", "spending": "sdot", "competition": "sdot"}
        status = dict(sdot.get("source_status") or {})
        status.update({"provider": "sdot", "fallback": bool(OA21285_FOOTFALL_ENABLED)})
        if OA21285_FOOTFALL_ENABLED:
            status.setdefault("fallback_reason", "oa_current_date_time_band_unavailable")
        sdot["source_status"] = status
        return sdot
    combined = dict(oa_footfall)
    combined.pop("provider", None)
    combined["footfall_sources"] = {"inflow": "oa21285", "spending": "oa21285", "competition": "sdot"}
    combined["competition_footfall"] = {
        "summary": {"visitor_spike_ratio": (sdot.get("summary") or {}).get("visitor_spike_ratio")},
        "source_status": sdot.get("source_status", {}),
    }
    return combined


def resolve_indicator_footfall_sources(oa_footfall: dict[str, Any] | None,
                                       sdot_loader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Resolve the single-request hybrid input with exactly one lazy S-DoT fetch."""
    return combine_indicator_footfall_sources(oa_footfall, sdot_loader())
