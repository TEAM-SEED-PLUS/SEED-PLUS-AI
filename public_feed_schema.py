"""Stable public Feed Output Schema v1 serializer.

The production pipeline remains the internal/debug contract.  This module only
projects its result into the credential-free contract consumed by a Backend.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from common import SEOUL_TZ
    from market_feed_pipeline import generate_market_feed
except ImportError:  # pragma: no cover
    from .common import SEOUL_TZ
    from .market_feed_pipeline import generate_market_feed

SCHEMA_VERSION = "1.0"
NIGHT_BADGE = "심야 시간대는 데이터가 제한적입니다 (06시부터 갱신)"
INSUFFICIENT_BADGE = "일부 데이터가 부족해 대체값 또는 기본값을 사용했습니다"


def _clean_status(value: Any, default: str = "no_data") -> str:
    status = str(value or default)
    return status if status in {"ok", "partial", "fallback", "no_data", "failed", "complete"} else default


def _source(source: str, metadata: dict[str, Any] | None, **fields: Any) -> dict[str, Any]:
    metadata = metadata or {}
    item = {"source": source, "status": _clean_status(metadata.get("status"))}
    item.update({key: value for key, value in fields.items() if value is not None})
    if metadata.get("fallback") is not None:
        item["fallback"] = bool(metadata["fallback"])
    return item


def _public_sources(result: dict[str, Any]) -> dict[str, Any]:
    statuses = result.get("source_status") or {}
    footfall = statuses.get("footfall") or {}
    tourism = statuses.get("tourism_baseline") or {}
    commercial = statuses.get("commercial_store_competition") or {}
    consumption = statuses.get("consumption_hybrid") or {}
    normalized = result.get("normalized_data") or {}
    hybrid = normalized.get("consumption_hybrid") or {}
    baseline, realtime = hybrid.get("baseline") or {}, hybrid.get("realtime") or {}
    footfall_sources = (normalized.get("footfall") or {}).get("footfall_sources") or {}
    competition_status = ((normalized.get("footfall") or {}).get("competition_footfall") or {}).get("source_status") or footfall

    return {
        "weather": _source("weather", statuses.get("weather")),
        "content": {
            name: _source(name, statuses.get(name))
            for name in ("festival", "event", "performance", "sports")
        },
        "special_day": _source("special_day", statuses.get("special_day")),
        "footfall": _source(
            str(footfall_sources.get("inflow") or footfall.get("provider") or "sdot"), footfall,
            source_time=footfall.get("latest_population_time"),
            snapshot_count=footfall.get("snapshot_count"),
        ),
        "tourism": _source(
            "tourapi", tourism, requested_month=tourism.get("requested_month"),
            source_month=tourism.get("source_month"), age_months=tourism.get("age_months"),
        ),
        "commercial_store": _source(
            "commercial_store", commercial, mode=commercial.get("mode"),
            requested_quarter=commercial.get("requested_quarter"),
            source_quarter=commercial.get("source_quarter"), age_quarters=commercial.get("age_quarters"),
        ),
        "consumption_baseline": _source(
            str(baseline.get("source") or "oa22176_oa22173"),
            {"status": baseline.get("source_status")},
            requested_quarter=baseline.get("requested_quarter"), source_quarter=baseline.get("source_quarter"),
            age_quarters=baseline.get("age_quarters"),
        ),
        "realtime_commerce": _source(
            "oa22385", {"status": realtime.get("source_status")},
            source_time=realtime.get("commerce_time"), age_minutes=realtime.get("age_minutes"),
            valid_place_count=realtime.get("valid_place_count"),
        ),
        "competition_sdot": _source("sdot", competition_status),
    }


def _quality_status(result: dict[str, Any], sources: dict[str, Any]) -> str:
    if not result.get("data_insufficient"):
        return "ok"
    flat = [value for value in sources.values() if isinstance(value, dict) and "status" in value]
    flat.extend((sources.get("content") or {}).values())
    statuses = {str(item.get("status")) for item in flat}
    if "fallback" in statuses or "partial" in statuses:
        return "fallback" if statuses <= {"ok", "complete", "fallback"} else "partial"
    if statuses <= {"no_data", "failed"}:
        return "no_data"
    return "partial"


def serialize_public_feed(result: dict[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    """Project one internal pipeline result into the immutable public v1 shape."""
    query, card, scores = result.get("query") or {}, result.get("card") or {}, result.get("scores") or {}
    sources = _public_sources(result)
    fallback_sources: list[str] = []
    for name, value in sources.items():
        if name == "content":
            fallback_sources.extend(f"content.{key}" for key, item in value.items()
                                    if item.get("status") in {"fallback", "failed", "no_data", "partial"})
        elif value.get("status") in {"fallback", "failed", "no_data", "partial"} or value.get("fallback"):
            fallback_sources.append(name)
    badges = []
    if result.get("notice") == NIGHT_BADGE:
        badges.append(NIGHT_BADGE)
    if result.get("data_insufficient"):
        badges.append(INSUFFICIENT_BADGE)
    score_context = result.get("score_context") or {}
    public_context = {
        key: score_context.get(key) for key in (
            "basis", "reference_date", "reference_time_band", "reference_start",
            "reference_end", "representative_time",
        ) if score_context.get(key) is not None
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "query": {key: query.get(key) for key in ("district", "date", "time", "time_band")},
        "opportunity_score": scores.get("opportunity_score"),
        "market_weather": {"score": scores.get("opportunity_score"), "grade": card.get("market_weather"),
                           "emoji": card.get("weather_icon")},
        "indicators": {key: scores.get(key) for key in (
            "inflow_pressure", "spending_intent", "competition_pressure", "operational_risk")},
        "decision_tags": list(result.get("decision_tags") or []),
        "narrative": {"generation_mode": result.get("generation_mode") or "rule_fallback",
                      "judgement_sentence": str(card.get("judgement_sentence") or ""),
                      "basis_sentence": str(card.get("basis_sentence") or ""),
                      "recommended_actions": list(card.get("recommended_actions") or [])},
        "data_quality": {"status": _quality_status(result, sources),
                         "data_insufficient": bool(result.get("data_insufficient")),
                         "badges": badges, "fallback_sources": fallback_sources,
                         "score_context": public_context},
        "sources": sources,
        "generated_at": generated_at or datetime.now(SEOUL_TZ).isoformat(timespec="seconds"),
    }


def generate_public_market_feed(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run the existing production path, then return only its public projection."""
    return serialize_public_feed(generate_market_feed(*args, **kwargs))

