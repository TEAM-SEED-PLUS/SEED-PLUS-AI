"""Stable public Feed Output Schema v1 serializer.

The production pipeline remains the internal/debug contract.  This module only
projects its result into the credential-free contract consumed by a Backend.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

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
    return status if status in {"ok", "empty", "partial", "fallback", "no_data", "failed", "complete"} else default


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
            name: _source(name, statuses.get(name), item_count=(normalized.get(name) or {}).get("count"),
                          source_time=(statuses.get(name) or {}).get("source_time"),
                          age_minutes=(statuses.get(name) or {}).get("age_minutes"),
                          eligibility_reason=(statuses.get(name) or {}).get("eligibility_reason"))
            for name in ("festival", "event", "performance", "sports")
        },
        "special_day": _source(
            "special_day", statuses.get("special_day"),
            item_count=(normalized.get("special_day") or {}).get("count"),
        ),
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
            eligibility_reason=realtime.get("eligibility_reason"),
        ),
        "competition_sdot": _source("sdot", competition_status),
    }


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _date_text(value: Any) -> str | None:
    value = _text(value)
    if not value:
        return None
    compact = value.replace(".", "").replace("-", "").replace("/", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return value


_CREDENTIAL_QUERY_KEYS = {
    "apikey", "api_key", "servicekey", "service_key", "secret", "client_secret",
    "access_token", "token", "authorization", "auth",
}


def _safe_public_url(value: Any) -> str | None:
    """Allow only absolute web links that cannot expose provider credentials."""
    value = _text(value)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if query_keys & _CREDENTIAL_QUERY_KEYS:
        return None
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if hostname in {"apis.data.go.kr", "openapi.seoul.go.kr"}:
        return None
    if hostname.endswith("kopis.or.kr") and "/openapi/" in path:
        return None
    return value


def _content_period(item: dict[str, Any]) -> str | None:
    start, end, time_text = (_date_text(item.get("start_date")),
                             _date_text(item.get("end_date")), _text(item.get("time_text")))
    # Seoul event DATE fields sometimes already contain the complete range and
    # the same text is also copied into the time field. Treat it as one period.
    combined = start or ""
    dates = re.findall(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", combined)
    if len(dates) >= 2:
        first, last = dates[0], dates[-1]
        return (f"{int(first[0]):04d}-{int(first[1]):02d}-{int(first[2]):02d} ~ "
                f"{int(last[0]):04d}-{int(last[1]):02d}-{int(last[2]):02d}")
    if start and end and start != end:
        return f"{start} ~ {end}"
    date_value = start or end
    if date_value and time_text and time_text != date_value and not re.search(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}", time_text):
        return f"{date_value} {time_text}"
    return date_value or time_text


def _public_content(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Whitelist display fields from the already-normalized content blocks."""
    output = []
    normalized = result.get("normalized_data") or {}
    for kind in ("festival", "event", "performance", "sports"):
        for item in (normalized.get(kind) or {}).get("items") or []:
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            identifier = None
            if kind == "performance":
                identifier = raw.get("mt20id")
            elif kind in {"festival", "event"}:
                identifier = raw.get("contentid") or raw.get("content_id") or raw.get("event_id") or raw.get("id")
            elif kind == "sports":
                identifier = raw.get("id") or raw.get("game_id")
            if not _text(identifier):
                seed = "|".join(str(raw.get(key) or item.get(key) or "") for key in (
                    "date", "league", "time", "match", "stadium", "title", "place"))
                identifier = hashlib.sha256(f"{kind}|{seed}".encode("utf-8")).hexdigest()
            thumbnail = (raw.get("poster") if kind == "performance" else
                         raw.get("firstimage") or raw.get("image") or raw.get("thumbnail"))
            output.append({"id": str(identifier), "type": kind,
                           "title": _text(item.get("title")) or "",
                           "period": _content_period(item),
                           "place": _text(item.get("place")),
                           "thumbnail_url": _text(thumbnail),
                           "link_url": _safe_public_url(item.get("link_url"))})
    return {"items": output}


def _flat_sources(sources: dict[str, Any]):
    for name, value in sources.items():
        if name == "content":
            for child_name, child in value.items():
                yield f"content.{child_name}", child
        else:
            yield name, value


def _quality_groups(sources: dict[str, Any]) -> dict[str, list[str]]:
    groups = {key: [] for key in (
        "fallback_sources", "no_data_sources", "stale_sources",
        "skipped_sources", "failed_sources", "empty_sources",
    )}
    for name, source in _flat_sources(sources):
        status = source.get("status")
        reason = str(source.get("fallback_reason") or "")
        eligibility = str(source.get("eligibility_reason") or "")
        requested = source.get("requested_month") or source.get("requested_quarter")
        actual = source.get("source_month") or source.get("source_quarter")

        if status == "failed":
            groups["failed_sources"].append(name)
        elif status == "empty":
            groups["empty_sources"].append(name)
        elif eligibility == "stale_cache":
            groups["stale_sources"].append(name)
        elif eligibility in {"different_date_or_time_band", "ineligible"}:
            groups["skipped_sources"].append(name)
        elif reason == "fallback_age_exceeded" or (
            status == "no_data" and requested and actual and source.get("age_months") is not None
        ):
            groups["stale_sources"].append(name)
        elif status == "fallback":
            groups["fallback_sources"].append(name)
        elif status in {"no_data", "partial"}:
            groups["no_data_sources"].append(name)
    return groups


def _quality_status(result: dict[str, Any], groups: dict[str, list[str]]) -> str:
    if not result.get("data_insufficient"):
        return "ok"
    shortage = (groups["no_data_sources"] + groups["stale_sources"]
                + groups["skipped_sources"] + groups["failed_sources"])
    if shortage:
        if (groups["no_data_sources"] and not groups["fallback_sources"]
                and not groups["stale_sources"] and not groups["skipped_sources"]
                and not groups["failed_sources"]):
            return "no_data"
        return "partial"
    if groups["fallback_sources"]:
        return "fallback"
    if result.get("data_insufficient"):
        # A higher-level production policy can mark a source combination as
        # insufficient even when each public component is individually valid.
        return "partial"
    if groups["no_data_sources"]:
        return "no_data"
    return "ok"


def serialize_public_feed(result: dict[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    """Project one internal pipeline result into the immutable public v1 shape."""
    query, card, scores = result.get("query") or {}, result.get("card") or {}, result.get("scores") or {}
    sources = _public_sources(result)
    quality_groups = _quality_groups(sources)
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
        "data_quality": {"status": _quality_status(result, quality_groups),
                         "data_insufficient": bool(result.get("data_insufficient")),
                         "badges": badges, **quality_groups,
                         "score_context": public_context},
        "sources": sources,
        "content": _public_content(result),
        "generated_at": generated_at or datetime.now(SEOUL_TZ).isoformat(timespec="seconds"),
    }


def generate_public_market_feed(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run the existing production path, then return only its public projection."""
    return serialize_public_feed(generate_market_feed(*args, **kwargs))
