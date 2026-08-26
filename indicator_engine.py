
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

try:
    from common import normalize_text
    from v1_config import (
        MAX_TOURISM_FALLBACK_MONTHS,
        FOOTFALL_AVG_INFLOW_CAP,
        FOOTFALL_AVG_SPENDING_CAP,
        FOOTFALL_COMPETITION_CAP,
        FOOTFALL_MAX_INFLOW_CAP,
        FOOTFALL_SPIKE_MULTIPLIER,
        COMMERCIAL_STORE_COMPETITION_CAP,
        COMMERCIAL_STORE_COMPETITION_ENABLED,
        CONSUMPTION_HYBRID_ENABLED,
        SALES_BASELINE_SPENDING_CAP,
        REALTIME_COMMERCE_SPENDING_CAP,
        SDOT_REALTIME_COMPETITION_CAP,
        STRUCTURAL_CLOSURE_RISK_ENABLED,
        STRUCTURAL_CLOSURE_RISK_POLICY,
        OPPORTUNITY_WEIGHTS,
        TAG_THRESHOLDS,
        TOURISM_INFLOW_BONUS_CAP,
        TOURISM_SCORING_ENABLED,
        TOURISM_SPENDING_BONUS_CAP,
        TOURISM_V1_COMPONENT_POLICY,
        TOURISM_V1_INFLOW_WEIGHTS,
        TOURISM_V1_SPENDING_WEIGHTS,
        WEATHER_THRESHOLDS,
    )
except ImportError:  # pragma: no cover
    from .common import normalize_text
    from .v1_config import (
        MAX_TOURISM_FALLBACK_MONTHS,
        FOOTFALL_AVG_INFLOW_CAP,
        FOOTFALL_AVG_SPENDING_CAP,
        FOOTFALL_COMPETITION_CAP,
        FOOTFALL_MAX_INFLOW_CAP,
        FOOTFALL_SPIKE_MULTIPLIER,
        COMMERCIAL_STORE_COMPETITION_CAP,
        COMMERCIAL_STORE_COMPETITION_ENABLED,
        CONSUMPTION_HYBRID_ENABLED,
        SALES_BASELINE_SPENDING_CAP,
        REALTIME_COMMERCE_SPENDING_CAP,
        SDOT_REALTIME_COMPETITION_CAP,
        STRUCTURAL_CLOSURE_RISK_ENABLED,
        STRUCTURAL_CLOSURE_RISK_POLICY,
        OPPORTUNITY_WEIGHTS,
        TAG_THRESHOLDS,
        TOURISM_INFLOW_BONUS_CAP,
        TOURISM_SCORING_ENABLED,
        TOURISM_SPENDING_BONUS_CAP,
        TOURISM_V1_COMPONENT_POLICY,
        TOURISM_V1_INFLOW_WEIGHTS,
        TOURISM_V1_SPENDING_WEIGHTS,
        WEATHER_THRESHOLDS,
    )

MANDATORY_TAGS = [
    "진입 유리",
    "관망 권장",
    "리스크 주의",
    "특정 업종만 유리",
    "과열 상태",
    "기회 구간",
]

WEATHER_EMOJI = {
    "맑음": "☀️",
    "구름": "⛅",
    "흐림": "☁️",
    "비": "🌧️",
    "폭풍": "⛈️",
}


@dataclass
class IndicatorResult:
    inflow_pressure: int
    spending_intent: int
    competition_pressure: int
    operational_risk: int
    opportunity_score: int
    market_weather: str
    market_weather_emoji: str
    decision_tags: list[str]
    evidence: dict[str, Any]
    signal_flags: list[str]
    recommended_categories: list[str]
    avoid_reasons: list[str]
    contribution_map: dict[str, Any]


def clamp(v: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(max(low, min(high, round(v))))


def classify_market_weather(opportunity_score: int) -> str:
    if opportunity_score >= WEATHER_THRESHOLDS["clear"]:
        return "맑음"
    if opportunity_score >= WEATHER_THRESHOLDS["cloud"]:
        return "구름"
    if opportunity_score >= WEATHER_THRESHOLDS["overcast"]:
        return "흐림"
    if opportunity_score >= WEATHER_THRESHOLDS["rain"]:
        return "비"
    return "폭풍"


def determine_decision_tags(
    inflow: int,
    spending: int,
    competition: int,
    risk: int,
    opportunity: int,
    recommended_categories: list[str] | None = None,
    qualifying_content_count: int = 0,
) -> list[str]:
    """v1 경계값만 담당하는 결정 함수. LLM은 이 결과를 변경할 수 없다."""
    entry_favorable = (
        opportunity >= TAG_THRESHOLDS["entry_opportunity"]
        and competition < TAG_THRESHOLDS["entry_competition_max_exclusive"]
        and risk < TAG_THRESHOLDS["entry_risk_max_exclusive"]
    )
    opportunity_zone = (
        inflow >= TAG_THRESHOLDS["inflow_opportunity"]
        and spending >= TAG_THRESHOLDS["spending_opportunity"]
    )
    tags: list[str] = []
    if entry_favorable:
        tags.append("진입 유리")
    if opportunity_zone:
        tags.append("기회 구간")
    if competition >= TAG_THRESHOLDS["overheated_competition"]:
        tags.append("과열 상태")
    if risk >= TAG_THRESHOLDS["risk_caution"]:
        tags.append("리스크 주의")
    spread = max(inflow, spending, competition, risk) - min(inflow, spending, competition, risk)
    if (
        recommended_categories
        and qualifying_content_count >= TAG_THRESHOLDS["category_content_count"]
        and spread >= TAG_THRESHOLDS["category_spread"]
    ):
        tags.append("특정 업종만 유리")
    if not entry_favorable and not opportunity_zone:
        tags.append("관망 권장")
    return tags


def _f(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except Exception:
        return None


def _weather_state(summary: str) -> str:
    text = normalize_text(summary)
    if any(k in text for k in ["뇌우", "폭우", "폭설"]):
        return "storm"
    if any(k in text for k in ["비", "눈", "소나기"]):
        return "rain"
    if any(k in text for k in ["흐림", "안개"]):
        return "cloudy"
    if any(k in text for k in ["구름", "구름많음", "부분적으로 흐림"]):
        return "partly"
    return "clear"


def _content_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for key in ["festival", "event", "performance", "sports"]:
        items.extend(data.get(key, {}).get("items", []) or [])
    return [x for x in items if isinstance(x, dict)]


def _tag_count(data: dict[str, Any], tag: str) -> int:
    return int((data.get("overview", {}).get("content_tag_counts", {}) or {}).get(tag, 0) or 0)


def _content_contributions(data: dict[str, Any]) -> dict[str, float]:
    items = _content_items(data)
    festival_count = int(data.get("festival", {}).get("count", 0) or 0)
    event_count = int(data.get("event", {}).get("count", 0) or 0)
    performance_count = int(data.get("performance", {}).get("count", 0) or 0)
    sports_count = int(data.get("sports", {}).get("count", 0) or 0)

    seasonal_hits = _tag_count(data, "seasonal")
    outdoor_hits = _tag_count(data, "outdoor")
    indoor_hits = _tag_count(data, "indoor")
    sports_hits = _tag_count(data, "sports")
    premium_hits = _tag_count(data, "premium")
    family_hits = _tag_count(data, "family")

    inflow = min(26.0, festival_count * 5.0 + event_count * 3.0 + sports_count * 4.0 + seasonal_hits * 2.0 + outdoor_hits * 1.2)
    spending = min(24.0, performance_count * 4.5 + premium_hits * 2.6 + event_count * 2.2 + family_hits * 1.4)
    competition = min(25.0, sports_count * 6.0 + festival_count * 3.5 + len(items) * 1.3)
    risk = min(12.0, outdoor_hits * 1.7 + sports_count * 1.8 + festival_count * 1.3)

    return {
        "festival_count": festival_count,
        "event_count": event_count,
        "performance_count": performance_count,
        "sports_count": sports_count,
        "seasonal_hits": seasonal_hits,
        "outdoor_hits": outdoor_hits,
        "indoor_hits": indoor_hits,
        "sports_hits": sports_hits,
        "premium_hits": premium_hits,
        "family_hits": family_hits,
        "inflow_bonus": inflow,
        "spending_bonus": spending,
        "competition_bonus": competition,
        "risk_bonus": risk,
    }


def count_qualifying_content(data: dict[str, Any]) -> int:
    """v1 특정 업종 태그에 사용하는 축제·행사·공연·스포츠 전체 건수."""
    return sum(int((data.get(name, {}) or {}).get("count", 0) or 0) for name in (
        "festival", "event", "performance", "sports"
    ))


def _footfall_contributions(data: dict[str, Any]) -> dict[str, Any]:
    footfall = data.get("footfall", {}) or {}
    summary = footfall.get("summary", {}) or {}
    provider = str(footfall.get("provider") or (footfall.get("source_status") or {}).get("provider") or "sdot")
    sources = dict(footfall.get("footfall_sources") or {})
    if not sources:
        sources = {"inflow": "oa21285", "spending": "oa21285", "competition": "sdot"} if provider == "oa21285" else {
            "inflow": "sdot", "spending": "sdot", "competition": "sdot"
        }
    if sources.get("inflow") == "oa21285" or sources.get("spending") == "oa21285":
        supplied = footfall.get("contributions", {}) or {}
        level = max(0.0, min(FOOTFALL_AVG_INFLOW_CAP, _f(supplied.get("inflow_level_bonus")) or 0.0))
        peak = max(0.0, min(FOOTFALL_MAX_INFLOW_CAP, _f(supplied.get("inflow_peak_bonus")) or 0.0))
        spending = max(0.0, min(FOOTFALL_AVG_SPENDING_CAP, _f(supplied.get("spending_level_bonus")) or 0.0))
        competition_source = footfall.get("competition_footfall", {}) or {}
        competition = compute_competition_footfall_contribution(
            competition_source.get("summary", {}) or {}
        )
        return {
            "footfall_sources": sources, "normalization": "percentile",
            "population_raw_current": _f(summary.get("population_raw_current")),
            "population_raw_avg_in_band": _f(summary.get("population_raw_avg_in_band")),
            "population_raw_max_in_band": _f(summary.get("population_raw_max_in_band")),
            "population_percentile_avg_in_band": _f(summary.get("population_percentile_avg_in_band")),
            "population_percentile_max_in_band": _f(summary.get("population_percentile_max_in_band")),
            "population_percentile_min_in_band": _f(summary.get("population_percentile_min_in_band")),
            "spike": _f(summary.get("population_spike_ratio")),
            "snapshot_count": (footfall.get("source_status") or {}).get("snapshot_count"),
            "source_timestamps": summary.get("source_timestamps", []),
            "fallback": (footfall.get("source_status") or {}).get("fallback", False),
            "oa_temporal_spike_ratio": _f(summary.get("population_spike_ratio")),
            "oa_temporal_spike_used_for_competition": False,
            "oa_temporal_spike_reason": "semantic_scale_mismatch_with_sdot",
            "inflow_level_bonus": level, "inflow_peak_bonus": peak,
            "inflow_bonus": level + peak, "spending_bonus": spending,
            "competition_spike": competition["spike"],
            "competition_bonus": competition["bonus"],
            "competition_source_status": dict(competition_source.get("source_status") or {}),
        }
    avg = _f(summary.get("visitor_avg_in_band"))
    mx = _f(summary.get("visitor_max_in_band"))
    spike = _f(summary.get("visitor_spike_ratio"))
    inflow = 0.0
    spending = 0.0
    if avg is not None:
        inflow += min(FOOTFALL_AVG_INFLOW_CAP, math.log10(avg + 1) * 14.0)
        spending += min(FOOTFALL_AVG_SPENDING_CAP, math.log10(avg + 1) * 6.5)
    if mx is not None:
        inflow += min(FOOTFALL_MAX_INFLOW_CAP, math.log10(mx + 1) * 2.5)
    competition = compute_competition_footfall_contribution(summary)
    return {
        "provider": "sdot",
        "footfall_sources": sources,
        "normalization": "sdot_log10",
        "fallback": (footfall.get("source_status") or {}).get("fallback", False),
        "avg": avg,
        "max": mx,
        "spike": spike,
        "inflow_bonus": inflow,
        "spending_bonus": spending,
        "competition_bonus": competition["bonus"],
        "competition_source_status": dict(footfall.get("source_status") or {}),
    }


def compute_competition_footfall_contribution(summary: dict[str, Any]) -> dict[str, float | None]:
    """Current v1 competition proxy boundary; replaceable by a future measured provider."""
    spike = _f(summary.get("visitor_spike_ratio"))
    bonus = min(FOOTFALL_COMPETITION_CAP, max(0.0, spike - 1.0) * FOOTFALL_SPIKE_MULTIPLIER) if spike is not None else 0.0
    return {"provider": "sdot", "spike": spike, "bonus": bonus}


def compute_commercial_store_bonus(density_percentile: Any) -> float:
    value = _f(density_percentile)
    if value is None:
        return 0.0
    return round(max(0.0, min(COMMERCIAL_STORE_COMPETITION_CAP, value / 100 * COMMERCIAL_STORE_COMPETITION_CAP)), 4)


def compute_sdot_realtime_bonus(legacy_sdot_bonus: Any) -> float:
    value = _f(legacy_sdot_bonus) or 0.0
    scaled = value / FOOTFALL_COMPETITION_CAP * SDOT_REALTIME_COMPETITION_CAP
    return round(max(0.0, min(SDOT_REALTIME_COMPETITION_CAP, scaled)), 4)


def _quarter_for_date(value: Any) -> str | None:
    text = str(value or "")
    try:
        year, month = int(text[:4]), int(text[5:7] if "-" in text else text[4:6])
    except (TypeError, ValueError):
        return None
    return f"{year:04d}Q{(month - 1) // 3 + 1}" if 1 <= month <= 12 else None


def calculate_competition_contribution(data: dict[str, Any], footfall_metric: dict[str, Any]) -> dict[str, Any]:
    """Official v1 measured density + reduced S-DoT realtime contribution."""
    legacy_bonus = max(0.0, min(FOOTFALL_COMPETITION_CAP, _f(footfall_metric.get("competition_bonus")) or 0.0))
    spike = _f(footfall_metric.get("competition_spike") if "competition_spike" in footfall_metric else footfall_metric.get("spike"))
    sdot_status_value = str((footfall_metric.get("competition_source_status") or {}).get("status") or "")
    sdot_available = spike is not None and sdot_status_value not in {"failed", "no_data"}
    district = str((data.get("query") or {}).get("district") or "")
    requested_quarter = _quarter_for_date((data.get("query") or {}).get("date"))
    loaded: dict[str, Any] = {
        "data": None, "requested_quarter": requested_quarter, "source_quarter": None,
        "source_status": "failed", "age_quarters": None,
    }
    if COMMERCIAL_STORE_COMPETITION_ENABLED and district and requested_quarter:
        try:
            from commercial_store_snapshot import load_scoring_snapshot
            loaded = load_scoring_snapshot(requested_quarter)
        except Exception:
            loaded = {**loaded, "source_status": "failed"}
    snapshot = loaded.get("data") or {}
    store = ((snapshot.get("districts") or {}).get(district) or {})
    percentile = _f(store.get("density_percentile"))
    commercial_available = (
        COMMERCIAL_STORE_COMPETITION_ENABLED
        and loaded.get("source_status") in {"complete", "fallback"}
        and store.get("source_status") in {None, "ok"}
        and percentile is not None
    )
    commercial_bonus = compute_commercial_store_bonus(percentile) if commercial_available else 0.0
    realtime_bonus = compute_sdot_realtime_bonus(legacy_bonus) if sdot_available else 0.0
    if commercial_available and sdot_available:
        mode, applied, status = "commercial_plus_realtime", commercial_bonus + realtime_bonus, loaded["source_status"]
    elif commercial_available:
        mode, applied, status = "commercial_only", commercial_bonus, loaded["source_status"]
    elif sdot_available:
        mode, applied, status = "legacy_sdot_fallback", legacy_bonus, "fallback"
    else:
        mode, applied, status = "base_only", 0.0, "failed"
    return {
        "base": 18, "content_bonus": None,
        "store_count": store.get("store_count"),
        "district_area_km2": store.get("district_area_km2"),
        "stores_per_km2": store.get("stores_per_km2"),
        "density_percentile": percentile,
        "commercial_store_bonus": round(commercial_bonus, 4),
        "visitor_spike_ratio": spike,
        "legacy_sdot_bonus": round(legacy_bonus, 4),
        "sdot_realtime_bonus": round(realtime_bonus, 4),
        "applied_footfall_competition_bonus": round(applied, 4),
        "competition_mode": mode, "source_status": status,
        "data_insufficient": mode in {"commercial_only", "legacy_sdot_fallback", "base_only"},
        "requested_quarter": loaded.get("requested_quarter"),
        "source_quarter": loaded.get("source_quarter"), "age_quarters": loaded.get("age_quarters"),
        "snapshot_status": (snapshot.get("source_status") or loaded.get("source_status")),
        "closure_filter_applied": False,
        "closure_filter_reason": "business_registration_number_unavailable",
        "industry_competition_applied": False,
    }


def _weather_contributions(data: dict[str, Any]) -> dict[str, float]:
    weather = data.get("weather", {}) or {}
    summary = normalize_text(weather.get("summary"))
    state = _weather_state(summary)
    temp = _f(weather.get("temperature_c"))
    precip = _f(weather.get("precipitation_mm")) or 0.0
    wind = _f(weather.get("wind_speed_kmh")) or 0.0
    outdoor_hits = _tag_count(data, "outdoor")
    indoor_hits = _tag_count(data, "indoor")

    inflow = 0.0
    spending = 0.0
    risk = 0.0

    if state == "clear":
        inflow += 10
        spending += 6
    elif state == "partly":
        inflow += 6
        spending += 3
    elif state == "cloudy":
        risk += 6
    elif state == "rain":
        risk += 16
        inflow -= 8
        if indoor_hits > 0:
            spending += 4
        if outdoor_hits > 0:
            risk += min(8.0, outdoor_hits * 1.4)
            inflow -= min(6.0, outdoor_hits * 0.8)
    elif state == "storm":
        risk += 28
        inflow -= 15
        spending -= 6

    if precip >= 5:
        risk += 8
    elif precip >= 1:
        risk += 3
    if wind >= 25:
        risk += 8
    elif wind >= 15:
        risk += 4

    if temp is not None:
        if 12 <= temp <= 24:
            inflow += 7
            spending += 4
        elif 6 <= temp <= 28:
            inflow += 3
        elif temp <= 0 or temp >= 32:
            risk += 10
            inflow -= 4

    return {
        "summary": summary,
        "state": state,
        "temperature_c": temp,
        "precipitation_mm": precip,
        "wind_speed_kmh": wind,
        "indoor_hits": indoor_hits,
        "outdoor_hits": outdoor_hits,
        "inflow_bonus": inflow,
        "spending_bonus": spending,
        "risk_bonus": risk,
    }


def _special_day_contributions(data: dict[str, Any]) -> dict[str, float]:
    block = data.get("special_day", {}) or {}
    count = int(block.get("count", 0) or 0)
    holiday_count = int(block.get("holiday_count", 0) or 0)
    inflow = min(12.0, holiday_count * 8.0 + max(0, count - holiday_count) * 2.0)
    spending = min(10.0, holiday_count * 6.0 + max(0, count - holiday_count) * 1.5)
    risk = min(8.0, holiday_count * 3.0)
    return {
        "count": count,
        "holiday_count": holiday_count,
        "names": block.get("holiday_names", []) or [],
        "inflow_bonus": inflow,
        "spending_bonus": spending,
        "risk_bonus": risk,
    }


TOURISM_V1_SCORE_CODES = TOURISM_V1_COMPONENT_POLICY


def _month_distance(requested_month: str, source_month: str) -> int | None:
    try:
        requested_year, requested_value = int(requested_month[:4]), int(requested_month[4:6])
        source_year, source_value = int(source_month[:4]), int(source_month[4:6])
    except (TypeError, ValueError):
        return None
    return (requested_year - source_year) * 12 + requested_value - source_value


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def calculate_tourism_baseline_contribution(data: dict[str, Any]) -> dict[str, Any]:
    """TourAPI v1 월간 관광 index와 production bonus를 계산한다."""
    baseline = data.get("tourism_baseline") if isinstance(data.get("tourism_baseline"), dict) else {}
    requested_month = str(baseline.get("requested_month") or "")
    baseline_source_month = str(baseline.get("source_month") or requested_month)
    snapshot_status = str(baseline.get("snapshot_status") or "no_data")
    metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    components: dict[str, dict[str, Any]] = {}
    missing_codes: list[str] = []
    failed_codes: list[str] = []
    fallback_codes: list[str] = []

    group_by_code = {
        code: group
        for group, records in metrics.items() if isinstance(records, dict)
        for code in records
    }
    for code in [code for codes in TOURISM_V1_SCORE_CODES.values() for code in codes]:
        group = group_by_code.get(code)
        source = metrics.get(group, {}).get(code, {}) if group else {}
        record = {
            key: source.get(key) for key in (
                "indicator_name", "raw_value", "normalized_value", "source_month", "source_status",
                "fallback_reason",
            )
        }
        record["indicator_code"] = code
        status = str(source.get("source_status") or "no_data")
        record["source_status"] = status
        component_source_month = str(source.get("source_month") or baseline_source_month)
        age = _month_distance(requested_month, component_source_month)
        record["age_months"] = age
        if status == "fallback":
            fallback_codes.append(code)
        normalized = _f(source.get("normalized_value"))
        stale_fallback = status == "fallback" and (age is None or age < 0 or age > MAX_TOURISM_FALLBACK_MONTHS)
        if status not in {"ok", "fallback"} or normalized is None or stale_fallback:
            normalized = None
            if stale_fallback:
                record["fallback_reason"] = "fallback_age_exceeded"
            if status == "failed":
                failed_codes.append(code)
            else:
                missing_codes.append(code)
        record["usable_normalized_value"] = normalized
        components[code] = record

    def score(codes: list[str]) -> float | None:
        return _mean([
            float(components[code]["usable_normalized_value"])
            for code in codes if components[code]["usable_normalized_value"] is not None
        ])

    stay_score = score(TOURISM_V1_SCORE_CODES["stay"])
    foreign_score = score(TOURISM_V1_SCORE_CODES["foreign_visitor"])
    culture_score = score(TOURISM_V1_SCORE_CODES["culture"])
    spending_score = score(TOURISM_V1_SCORE_CODES["spending"])
    food_score = score(TOURISM_V1_SCORE_CODES["food"])
    age_spending_diversity_score = score(TOURISM_V1_SCORE_CODES["age_spending_diversity"])

    def weighted_index(component_scores: dict[str, float | None], weights: dict[str, float]) -> float | None:
        available = [name for name, value in component_scores.items() if value is not None]
        if not available:
            return None
        # Missing component weights are intentionally not redistributed in v1.
        return sum(float(component_scores[name]) * weights[name] for name in available)

    inflow_index = weighted_index(
        {"stay": stay_score, "foreign_visitor": foreign_score, "culture": culture_score},
        TOURISM_V1_INFLOW_WEIGHTS,
    )
    spending_index = weighted_index(
        {"spending": spending_score, "food": food_score, "age_spending_diversity": age_spending_diversity_score},
        TOURISM_V1_SPENDING_WEIGHTS,
    )
    inflow_bonus = max(0.0, min(TOURISM_INFLOW_BONUS_CAP, (inflow_index or 0.0) / 100 * TOURISM_INFLOW_BONUS_CAP))
    spending_bonus = max(0.0, min(TOURISM_SPENDING_BONUS_CAP, (spending_index or 0.0) / 100 * TOURISM_SPENDING_BONUS_CAP))

    usable_count = sum(1 for item in components.values() if item["usable_normalized_value"] is not None)
    if usable_count == 0:
        source_status = "failed" if failed_codes else "no_data"
    elif usable_count < len(components) or snapshot_status != "complete":
        source_status = "partial"
    elif fallback_codes or baseline.get("source_status") == "fallback":
        source_status = "fallback"
    else:
        source_status = "ok"

    return {
        "requested_month": requested_month,
        "source_month": baseline_source_month,
        "age_months": _month_distance(requested_month, baseline_source_month),
        "snapshot_status": snapshot_status,
        "source_status": source_status,
        "data_quality": source_status,
        "scoring_enabled": TOURISM_SCORING_ENABLED,
        "policy_status": "official_v1",
        "max_fallback_months": MAX_TOURISM_FALLBACK_MONTHS,
        "fallback_reason": baseline.get("fallback_reason"),
        "missing_components": missing_codes,
        "failed_components": failed_codes,
        "fallback_components": fallback_codes,
        "inflow": {
            "stay_score": round(stay_score, 2) if stay_score is not None else None,
            "foreign_visitor_score": round(foreign_score, 2) if foreign_score is not None else None,
            "culture_score": round(culture_score, 2) if culture_score is not None else None,
            "weights": TOURISM_V1_INFLOW_WEIGHTS,
            "index": round(inflow_index, 2) if inflow_index is not None else None,
            "bonus": round(inflow_bonus, 2),
            "applied_bonus": round(inflow_bonus, 2) if TOURISM_SCORING_ENABLED else 0.0,
            "components": {code: components[code] for code in (
                TOURISM_V1_SCORE_CODES["stay"] + TOURISM_V1_SCORE_CODES["foreign_visitor"] + TOURISM_V1_SCORE_CODES["culture"]
            )},
        },
        "spending": {
            "spending_score": round(spending_score, 2) if spending_score is not None else None,
            "food_score": round(food_score, 2) if food_score is not None else None,
            "age_spending_diversity_score": round(age_spending_diversity_score, 2) if age_spending_diversity_score is not None else None,
            "weights": TOURISM_V1_SPENDING_WEIGHTS,
            "index": round(spending_index, 2) if spending_index is not None else None,
            "bonus": round(spending_bonus, 2),
            "applied_bonus": round(spending_bonus, 2) if TOURISM_SCORING_ENABLED else 0.0,
            "components": {code: components[code] for code in (
                TOURISM_V1_SCORE_CODES["spending"] + TOURISM_V1_SCORE_CODES["food"] + TOURISM_V1_SCORE_CODES["age_spending_diversity"]
            )},
        },
    }


def _recommended_categories(data: dict[str, Any], metrics: dict[str, float]) -> list[str]:
    tags = data.get("overview", {}).get("content_tag_counts", {}) or {}
    weather_state = metrics["weather"]["state"]
    recs: list[str] = []
    if tags.get("sports", 0) or metrics["content"]["sports_count"] > 0:
        recs += ["음식점", "카페", "간편식"]
    if tags.get("seasonal", 0) or metrics["content"]["festival_count"] > 0:
        recs += ["디저트", "음료", "편의형 소매"]
    if tags.get("premium", 0) or metrics["content"]["performance_count"] > 0:
        recs += ["예약형 외식", "카페", "라이프스타일 소매"]
    if weather_state in {"rain", "storm"}:
        recs += ["배달·포장", "실내형 서비스"]
    if weather_state in {"clear", "partly"} and tags.get("outdoor", 0):
        recs += ["야외형 F&B", "산책형 소비"]
    deduped = []
    for r in recs:
        if r not in deduped:
            deduped.append(r)
    return deduped[:5]


def _build_evidence(data: dict[str, Any], scores: dict[str, int], metrics: dict[str, Any]) -> dict[str, Any]:
    overview = data.get("overview", {}) or {}
    reason_summary = []
    weather = metrics["weather"]
    if weather.get("summary"):
        reason_summary.append(f"날씨는 {weather['summary']}이며 기온은 {weather.get('temperature_c')}°C입니다.")
    if metrics["special_day"].get("names"):
        reason_summary.append("특일/공휴일: " + ", ".join(metrics["special_day"]["names"][:3]))
    if overview.get("story_digest"):
        reason_summary.append("주요 현장 콘텐츠: " + "; ".join(overview["story_digest"][:3]))
    footfall_avg = metrics["footfall"].get("avg")
    if footfall_avg is not None:
        reason_summary.append(f"해당 시간대 유동인구 평균은 {footfall_avg:.2f}입니다.")
    if (metrics["footfall"].get("footfall_sources") or {}).get("inflow") == "oa21285":
        reason_summary.append(
            "OA-21285 유동값은 동일 시점 서울 자치구 내 상대 위치를 이용한 지표이며, "
            "raw population을 정확한 방문자 수로 단정하지 않습니다."
        )
    consumption = metrics.get("consumption_hybrid", {}) or {}
    baseline = consumption.get("baseline") or {}
    realtime = consumption.get("realtime") or {}
    if baseline.get("source_status") in {"ok", "fallback"}:
        reason_summary.append(
            "소비 기저는 서울시 추정매출과 동일 업종 모집단 점포수를 이용한 자치구 상대 소비 수준입니다."
        )
    if realtime.get("source_status") == "ok":
        reason_summary.append(
            "당일 보정은 서울 실시간 상권현황의 카드 결제금액 범위를 이용한 상대적 실시간 소비 활성 수준이며, "
            "서울 전체 소비를 완전히 대표하지 않습니다."
        )
    tourism = metrics.get("tourism_baseline", {}) or {}
    tourism_month = str(tourism.get("requested_month") or "")
    inflow_index = (tourism.get("inflow") or {}).get("index")
    spending_index = (tourism.get("spending") or {}).get("index")
    if tourism_month and (inflow_index is not None or spending_index is not None):
        month_label = f"{tourism_month[:4]}년 {int(tourism_month[4:6])}월" if len(tourism_month) == 6 else tourism_month
        reason_summary.append(
            f"이 자치구의 {month_label} 월간 관광 기저 상대지수는 "
            f"유입 {inflow_index if inflow_index is not None else '자료 없음'}, "
            f"소비 {spending_index if spending_index is not None else '자료 없음'}입니다."
        )
    competition = metrics.get("competition", {}) or {}
    density_percentile = competition.get("density_percentile")
    if density_percentile is not None:
        level = "높은" if density_percentile >= 67 else "낮은" if density_percentile <= 33 else "중간"
        reason_summary.append(
            f"자치구 내 전체 점포 밀도는 서울 25개구 대비 상대적으로 {level} 수준"
            f"(Percentile {density_percentile})입니다. 동일 업종 경쟁률이나 폐업률을 뜻하지 않습니다."
        )

    return {
        "scores": scores,
        "weather": metrics["weather"],
        "footfall": metrics["footfall"],
        "special_day": metrics["special_day"],
        "content": metrics["content"],
        "tourism_baseline": tourism,
        "tourism_baseline_snapshot": data.get("tourism_baseline", {}),
        "competition": metrics.get("competition", {}),
        "consumption_hybrid": consumption,
        "story_digest": overview.get("story_digest", [])[:8],
        "top_content_titles": overview.get("top_content_titles", [])[:8],
        "festival_samples": data.get("festival", {}).get("items", [])[:4],
        "event_samples": data.get("event", {}).get("items", [])[:4],
        "performance_samples": data.get("performance", {}).get("items", [])[:4],
        "sports_samples": data.get("sports", {}).get("items", [])[:4],
        "footfall_samples": data.get("footfall", {}).get("items", [])[:4],
        "reason_summary": reason_summary,
    }


def calculate_consumption_hybrid_contribution(data: dict[str, Any], footfall: dict[str, Any]) -> dict[str, Any]:
    """Replace, never add to, the legacy max-10 footfall spending budget."""
    legacy = max(0.0, min(FOOTFALL_AVG_SPENDING_CAP, float(footfall.get("spending_bonus") or 0.0)))
    supplied = data.get("consumption_hybrid") if isinstance(data.get("consumption_hybrid"), dict) else {}
    baseline = dict(supplied.get("baseline") or {})
    realtime = dict(supplied.get("realtime") or {})
    baseline_status = str(baseline.get("source_status") or "failed")
    realtime_status = str(realtime.get("source_status") or "no_data")
    baseline_bonus = max(0.0, min(SALES_BASELINE_SPENDING_CAP, float(baseline.get("bonus") or 0.0)))
    realtime_bonus = max(0.0, min(REALTIME_COMMERCE_SPENDING_CAP, float(realtime.get("bonus") or 0.0)))
    if not CONSUMPTION_HYBRID_ENABLED:
        mode, applied = "legacy_oa_spending_feature_disabled", legacy
    elif baseline_status in {"ok", "fallback"}:
        if realtime_status == "ok":
            mode, applied = "sales_baseline_plus_realtime", baseline_bonus + realtime_bonus
        else:
            mode, applied = "sales_baseline_only", baseline_bonus
    else:
        mode, applied = "legacy_oa_spending_fallback", legacy
    return {
        "enabled": CONSUMPTION_HYBRID_ENABLED, "mode": mode,
        "budget_cap": SALES_BASELINE_SPENDING_CAP + REALTIME_COMMERCE_SPENDING_CAP,
        "baseline": baseline, "realtime": realtime,
        "legacy_oa21285_fallback": {"available_bonus": legacy,
            "applied_bonus": legacy if mode in {"legacy_oa_spending_fallback", "legacy_oa_spending_feature_disabled"} else 0.0},
        "applied_bonus": round(min(FOOTFALL_AVG_SPENDING_CAP, applied), 4),
        "oa21285_spending_double_counted": False,
        "data_insufficient": mode in {"sales_baseline_only", "legacy_oa_spending_fallback"},
    }


def calculate_indicators(normalized_data: dict[str, Any]) -> IndicatorResult:
    metrics = {
        "content": _content_contributions(normalized_data),
        "footfall": _footfall_contributions(normalized_data),
        "weather": _weather_contributions(normalized_data),
        "special_day": _special_day_contributions(normalized_data),
        "tourism_baseline": calculate_tourism_baseline_contribution(normalized_data),
        "structural_risk": {
            **STRUCTURAL_CLOSURE_RISK_POLICY,
            "enabled": STRUCTURAL_CLOSURE_RISK_ENABLED,
            "applied_contribution": 0.0,
            "production_provider": None,
        },
    }
    metrics["competition"] = calculate_competition_contribution(normalized_data, metrics["footfall"])

    # 실패한 원천은 base를 유지하되 해당 원천의 bonus만 제외한다.
    status_map = normalized_data.get("source_status", {}) or {}
    for source, metric_key in {"weather": "weather", "special_day": "special_day"}.items():
        # A normal empty response is just as unavailable for scoring as a
        # transport failure.  In particular, an empty weather summary must not
        # fall through to the lexical default ("clear") and receive bonuses.
        if (status_map.get(source) or {}).get("status") in {"failed", "no_data"}:
            for key in list(metrics[metric_key]):
                if key.endswith("_bonus"):
                    metrics[metric_key][key] = 0.0
    if (status_map.get("footfall") or {}).get("status") == "failed":
        # Commercial competition is an independent quarterly source.  A failed
        # S-DoT request removes realtime/inflow/spending bonuses, not store density.
        for key in ("inflow_bonus", "spending_bonus"):
            metrics["footfall"][key] = 0.0
    failed_content = {
        name for name in ("festival", "event", "performance", "sports")
        if (status_map.get(name) or {}).get("status") == "failed"
    }
    if failed_content:
        # 정규화 단계에서 실패 블록은 빈 항목이므로 콘텐츠 bonus에는 자연히 반영되지 않는다.
        metrics["content"]["excluded_failed_sources"] = sorted(failed_content)

    metrics["consumption_hybrid"] = calculate_consumption_hybrid_contribution(
        normalized_data, metrics["footfall"])
    metrics["footfall"]["legacy_spending_bonus"] = metrics["footfall"].get("spending_bonus", 0.0)
    metrics["footfall"]["applied_spending_bonus"] = (
        metrics["consumption_hybrid"]["legacy_oa21285_fallback"]["applied_bonus"])

    inflow_pressure = clamp(
        24
        + metrics["content"]["inflow_bonus"]
        + metrics["footfall"]["inflow_bonus"]
        + metrics["weather"]["inflow_bonus"]
        + metrics["special_day"]["inflow_bonus"]
        + metrics["tourism_baseline"]["inflow"]["applied_bonus"]
    )
    spending_intent = clamp(
        24
        + metrics["content"]["spending_bonus"]
        + metrics["consumption_hybrid"]["applied_bonus"]
        + metrics["weather"]["spending_bonus"]
        + metrics["special_day"]["spending_bonus"]
        + metrics["tourism_baseline"]["spending"]["applied_bonus"]
    )
    competition_pressure = clamp(
        18
        + metrics["content"]["competition_bonus"]
        + metrics["competition"]["applied_footfall_competition_bonus"]
    )
    metrics["competition"].update({
        "content_bonus": metrics["content"]["competition_bonus"],
        "final": competition_pressure,
    })
    metrics["spending"] = {
        "base": 24, "content_bonus": metrics["content"]["spending_bonus"],
        "consumption_hybrid": metrics["consumption_hybrid"],
        "weather_bonus": metrics["weather"]["spending_bonus"],
        "special_day_bonus": metrics["special_day"]["spending_bonus"],
        "tourism_bonus": metrics["tourism_baseline"]["spending"]["applied_bonus"],
        "final": spending_intent,
    }
    operational_risk = clamp(
        20
        + metrics["content"]["risk_bonus"]
        + metrics["weather"]["risk_bonus"]
        + metrics["special_day"]["risk_bonus"]
    )

    opportunity_score = clamp(
        inflow_pressure * OPPORTUNITY_WEIGHTS["inflow"]
        + spending_intent * OPPORTUNITY_WEIGHTS["spending"]
        + (100 - competition_pressure) * OPPORTUNITY_WEIGHTS["inverse_competition"]
        + (100 - operational_risk) * OPPORTUNITY_WEIGHTS["inverse_risk"]
    )

    market_weather = classify_market_weather(opportunity_score)

    signal_flags: list[str] = []
    avoid_reasons: list[str] = []

    recommended_categories = _recommended_categories(normalized_data, metrics)
    qualifying_content_count = count_qualifying_content(normalized_data)
    decision_tags = determine_decision_tags(
        inflow_pressure, spending_intent, competition_pressure, operational_risk,
        opportunity_score, recommended_categories, qualifying_content_count,
    )

    if "기회 구간" in decision_tags:
        signal_flags.append("유입과 소비 전환 신호가 동시에 강합니다.")

    if "과열 상태" in decision_tags:
        signal_flags.append("수요는 강하지만 경쟁 밀도도 높은 구간입니다.")
        avoid_reasons.append("가격 경쟁보다 차별화와 회전율 관리가 우선입니다.")

    if "리스크 주의" in decision_tags:
        signal_flags.append("날씨·야외 이벤트·혼잡 변수로 운영 난도가 올라갑니다.")
        avoid_reasons.append("우천 대응, 인력 배치, 재고 보수 운영이 필요합니다.")

    if "특정 업종만 유리" in decision_tags:
        signal_flags.append("콘텐츠 성격상 수혜 업종이 뚜렷합니다.")

    deduped = []
    for tag in decision_tags:
        if tag in MANDATORY_TAGS and tag not in deduped:
            deduped.append(tag)
    decision_tags = deduped

    scores = {
        "inflow_pressure": inflow_pressure,
        "spending_intent": spending_intent,
        "competition_pressure": competition_pressure,
        "operational_risk": operational_risk,
        "opportunity_score": opportunity_score,
    }
    evidence = _build_evidence(normalized_data, scores, metrics)

    if metrics["weather"]["state"] in {"rain", "storm"} and metrics["content"]["outdoor_hits"] > 0:
        signal_flags.append("야외형 콘텐츠가 날씨 영향에 민감합니다.")
    if metrics["content"]["sports_count"] > 0:
        signal_flags.append("경기 전후 시간대 혼잡과 회전 수요가 예상됩니다.")
    if metrics["content"]["performance_count"] > 0:
        signal_flags.append("공연 관람 전후 정주형 소비가 붙을 가능성이 있습니다.")

    return IndicatorResult(
        inflow_pressure=inflow_pressure,
        spending_intent=spending_intent,
        competition_pressure=competition_pressure,
        operational_risk=operational_risk,
        opportunity_score=opportunity_score,
        market_weather=market_weather,
        market_weather_emoji=WEATHER_EMOJI[market_weather],
        decision_tags=decision_tags,
        evidence=evidence,
        signal_flags=list(dict.fromkeys(signal_flags))[:6],
        recommended_categories=recommended_categories,
        avoid_reasons=list(dict.fromkeys(avoid_reasons))[:4],
        contribution_map=metrics,
    )
