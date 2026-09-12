from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

try:
    from common import normalize_text
except ImportError:  # pragma: no cover
    from .common import normalize_text


def _pick(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = normalize_text(item.get(key))
        if value:
            return value
    return ""


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _normalize_weather(weather: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": normalize_text(weather.get("source")),
        "summary": normalize_text(weather.get("summary")),
        "forecast_time": normalize_text(weather.get("forecast_time")),
        "temperature_c": weather.get("temperature_c"),
        "humidity_percent": weather.get("humidity_percent"),
        "precipitation_probability_percent": weather.get("precipitation_probability_percent"),
        "precipitation_mm": weather.get("precipitation_mm"),
        "wind_speed_kmh": weather.get("wind_speed_kmh"),
        "fallback_reason": normalize_text(weather.get("fallback_reason")),
        "source_status": weather.get("source_status", {}),
    }


def _detect_content_tags(text: str) -> list[str]:
    lowered = normalize_text(text).lower()
    tags = []
    mapping = {
        "outdoor": ["벚꽃", "축제", "야시장", "공원", "호수", "광장", "한강", "플리마켓"],
        "indoor": ["전시", "뮤지컬", "연극", "클래식", "오페라", "박람회", "영화관", "미술관"],
        "sports": ["vs", "kbo", "k league", "kbl", "야구", "축구", "농구"],
        "family": ["가족", "체험", "어린이", "키즈"],
        "premium": ["오페라", "클래식", "파인다이닝", "전시", "뮤지컬"],
        "seasonal": ["벚꽃", "크리스마스", "불꽃", "페스티벌", "축제"],
    }
    for tag, keywords in mapping.items():
        if any(k in lowered for k in keywords):
            tags.append(tag)
    return tags


def _normalize_content_item(item: dict[str, Any], source_kind: str) -> dict[str, Any]:
    title = _pick(item, ["title", "match", "dateName", "summary"])
    category = _pick(item, ["category_name", "genre", "sport", "category", "source"])
    place = _pick(item, ["place", "stadium", "address", "administrative_district"])
    time_text = _pick(item, ["time_text", "time", "sensing_time", "date_text"])
    start_date = _pick(item, ["event_start_date", "date_from", "date", "date_text"])
    end_date = _pick(item, ["event_end_date", "date_to"])
    detail = _pick(item, ["runtime", "price_text", "use_fee", "match", "detail", "original_time_text"])
    tags = _detect_content_tags(" ".join([title, category, place, detail]))
    return {
        "kind": source_kind,
        "source": _pick(item, ["source", "league"]),
        "title": title,
        "category": category,
        "place": place,
        "time_text": time_text,
        "start_date": start_date,
        "end_date": end_date,
        "detail": detail,
        "tags": tags,
        "raw": item,
    }



def _user_content_brief(item: dict[str, Any]) -> str:
    """Build narrative text from user-meaningful fields, never provenance."""
    title = normalize_text(item.get("title"))
    place = normalize_text(item.get("place"))
    if not title:
        return ""
    return f"{title}({place})" if place and place not in title else title



def _normalize_content_block(block: dict[str, Any], source_kind: str) -> dict[str, Any]:
    items = [_normalize_content_item(x, source_kind) for x in _as_list(block.get("items")) if isinstance(x, dict)]
    top_titles = [x["title"] for x in items if x.get("title")][:5]
    top_categories = [x["category"] for x in items if x.get("category")][:5]
    tag_counter = Counter(tag for item in items for tag in item.get("tags", []))
    story_lines = []
    llm_briefs = []
    for item in items[:5]:
        line = _user_content_brief(item)
        if line:
            story_lines.append(line)
        brief = _user_content_brief(item)
        if brief:
            llm_briefs.append(brief)
    return {
        "count": int(block.get("count", len(items)) or 0),
        "items": items,
        "top_titles": top_titles,
        "top_categories": top_categories,
        "tag_counts": dict(tag_counter),
        "story_lines": story_lines,
        "llm_briefs": llm_briefs,
        "source_status": block.get("source_status", {}),
    }


def _normalize_special_day(block: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _as_list(block.get("items")):
        if not isinstance(item, dict):
            continue
        items.append({
            "category": normalize_text(item.get("category")),
            "name": normalize_text(item.get("dateName")),
            "is_holiday": normalize_text(item.get("isHoliday") or item.get("isholiday")),
            "date_kind": normalize_text(item.get("dateKind")),
        })
    holiday_names = [x["name"] for x in items if x.get("name")][:10]
    return {
        "count": len(items),
        "items": items,
        "holiday_names": holiday_names,
        "holiday_count": sum(1 for x in items if x.get("is_holiday") in {"Y", "1", "true", "True"}),
        "llm_briefs": [f"{x['category']} / {x['name']}" for x in items if x.get("name")][:10],
        "source_status": block.get("source_status", {}),
        "errors": block.get("errors", []),
    }


def _normalize_footfall(block: dict[str, Any]) -> dict[str, Any]:
    summary = block.get("summary", {}) if isinstance(block.get("summary"), dict) else {}
    sources = dict(block.get("footfall_sources") or {})
    provider = str(block.get("provider") or (block.get("source_status") or {}).get("provider") or "sdot")
    if sources.get("inflow") == "oa21285" or provider == "oa21285":
        return {
            "footfall_sources": sources or {"inflow": "oa21285", "spending": "oa21285", "competition": "sdot"},
            "district": normalize_text(block.get("district")),
            "time_band": normalize_text(block.get("time_band")), "count": int(block.get("count", 0) or 0),
            "items": [], "summary": dict(summary), "contributions": dict(block.get("contributions") or {}),
            "competition_footfall": dict(block.get("competition_footfall") or {}),
            "diagnostics": dict(block.get("diagnostics") or {}),
            "source_status": dict(block.get("source_status") or {}),
            "data_semantics": block.get("data_semantics"),
        }
    items = []
    for item in _as_list(block.get("items")):
        if not isinstance(item, dict):
            continue
        items.append({
            "title": normalize_text(item.get("administrative_district") or item.get("region")),
            "time_text": normalize_text(item.get("sensing_time")),
            "visitor_count": item.get("visitor_count"),
            "region": normalize_text(item.get("region")),
            "serial_no": normalize_text(item.get("serial_no")),
            "raw": item,
        })
    spike = None
    avg = summary.get("visitor_avg_in_band")
    mx = summary.get("visitor_max_in_band")
    try:
        if avg not in (None, 0, "", "None") and mx not in (None, "", "None"):
            spike = round(float(mx) / float(avg), 2)
    except Exception:
        spike = None
    return {
        "provider": provider,
        "footfall_sources": sources or {"inflow": "sdot", "spending": "sdot", "competition": "sdot"},
        "count": int(block.get("count", len(items)) or 0),
        "items": items,
        "summary": {
            "records_total": summary.get("records_total"),
            "records_in_band": summary.get("records_in_band"),
            "visitor_sum_in_band": summary.get("visitor_sum_in_band"),
            "visitor_avg_in_band": summary.get("visitor_avg_in_band"),
            "visitor_max_in_band": summary.get("visitor_max_in_band"),
            "visitor_min_in_band": summary.get("visitor_min_in_band"),
            "visitor_spike_ratio": spike,
        },
        "source_status": block.get("source_status", {}),
    }


def normalize_city_data(city_info: dict[str, Any]) -> dict[str, Any]:
    query = city_info.get("query", {}) if isinstance(city_info.get("query"), dict) else {}
    normalized = {
        "query": query,
        "weather": _normalize_weather(city_info.get("weather", {})),
        "special_day": _normalize_special_day(city_info.get("special_day", {})),
        "footfall": _normalize_footfall(city_info.get("footfall", {})),
        "festival": _normalize_content_block(city_info.get("festival", {}), "festival"),
        "event": _normalize_content_block(city_info.get("event", {}), "event"),
        "performance": _normalize_content_block(city_info.get("performance", {}), "performance"),
        "sports": _normalize_content_block(city_info.get("sports", {}), "sports"),
    }
    normalized["source_status"] = {
        key: normalized[key].get("source_status", {})
        for key in ("weather", "special_day", "footfall", "festival", "event", "performance", "sports")
    }
    normalized["data_insufficient"] = any(
        value.get("status") == "failed" for value in normalized["source_status"].values()
    )

    all_content = []
    for key in ["festival", "event", "performance", "sports"]:
        all_content.extend(normalized[key]["items"])

    normalized["overview"] = {
        "total_content_count": sum(normalized[key]["count"] for key in ["festival", "event", "performance", "sports"]),
        "top_content_titles": [x["title"] for x in all_content if x.get("title")][:10],
        "story_digest": [
            line
            for key in ["festival", "event", "performance", "sports"]
            for line in normalized[key].get("story_lines", [])[:2]
        ][:10],
        "llm_story_digest": [
            line
            for key in ["festival", "event", "performance", "sports"]
            for line in normalized[key].get("llm_briefs", [])[:3]
        ][:12],
        "content_tag_counts": dict(Counter(tag for item in all_content for tag in item.get("tags", []))),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }
    return normalized
