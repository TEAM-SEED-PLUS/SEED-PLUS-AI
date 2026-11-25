"""Coordinate-first Hongdae MVP pipeline with no weather dependency."""
from __future__ import annotations

from datetime import datetime
from dataclasses import replace
import os
from pathlib import Path
from typing import Any, Callable

from common import SEOUL_TZ, TIME_BAND_REPRESENTATIVE_TIMES, resolve_time_input
from hongdae_data import configured_population
from hongdae_providers import (AdministrativeDongBaselineProvider,
                               NoDataLivingPopulationProvider, collect_radius_stores)
from hongdae_scoring import calculate_hongdae_indicators
from location_context import HONGDAE_DEFAULT, LocationContext
from naver_location_api import NaverLocationClient, NaverMapsClient
from snapshot_cache import read_snapshot, write_snapshot
from hongdae_store_file import radius_inventory
from performance_cache import get_cached_performances
from sports_cache import get_cached_sports

TIME_BANDS = ("심야", "아침", "점심", "오후", "저녁")


def _content_no_data(_location: LocationContext, _date: str, _time_band: str) -> dict:
    return {"count": 0, "items": [], "source_status": "no_data"}


def _cached_content(location: LocationContext, date: str, time_band: str) -> dict:
    if not location.district:
        return _content_no_data(location, date, time_band)
    time = TIME_BAND_REPRESENTATIVE_TIMES[time_band]
    items = []
    statuses = {}
    for kind, getter in (("performance", get_cached_performances), ("sports", get_cached_sports)):
        result = getter(location.district, date, time)
        statuses[kind] = result.get("source_status", {}).get("status", "no_data")
        items.extend({"kind": kind, "title": row.get("title") or row.get("match"),
                      "spatial_grain": "district", "used_in_score": False}
                     for row in result.get("items", []))
    return {"count": 0, "items": items, "source_status": "partial" if items else "no_data",
            "source_statuses": statuses}


def _special_no_data(_date: str) -> dict:
    return {"is_special_day": False, "source_status": "no_data"}


def _narrative(score: float, baseline: dict, radius: dict) -> dict:
    period = baseline.get("metadata", {}).get("source_period")
    basis = f"최신 분기({period}) 기준" if period else "분기 기준 자료 미확보 상태에서"
    return {"generation_mode": "deterministic_fallback",
            "judgement_sentence": f"홍대입구 반경 상권 기회 점수는 {score}점입니다.",
            "basis_sentence": f"{basis} 분석했습니다. " + (
                f"반경 내 점포 {radius['total_store_count']}곳을 확인했습니다."
                if radius.get("source_status") == "ok" else "반경 점포 자료를 확보하지 못했습니다."),
            "recommended_actions": ["반경 내 동일업종 밀도를 확인하세요.", "확정된 생활인구가 들어오면 시간대별 운영을 조정하세요."]}


def snapshot_path(date: str, time_band: str, data_dir: str | Path = "data/hongdae/daily") -> Path:
    return Path(data_dir) / f"{date}_{time_band}.json"


def generate_hongdae_market_feed(
    date_str: str | None = None, time_str: str | None = None, time_band: str | None = None,
    *, location: LocationContext = HONGDAE_DEFAULT, store_client=None,
    naver_client=None, maps_client=None, historical=False,
    population_provider=None, baseline_provider=None,
    content_provider: Callable = _cached_content,
    special_day_provider: Callable = _special_no_data,
    data_dir: str | Path = "data/hongdae/daily", persist: bool = True,
) -> dict[str, Any]:
    now = datetime.now(SEOUL_TZ)
    date = date_str or now.strftime("%Y-%m-%d")
    resolved_time, band = resolve_time_input(time_str, time_band)
    if location is HONGDAE_DEFAULT:
        location = replace(location, radius_m=int(os.getenv("HONGDAE_RADIUS_M") or "300"))
    # NAVER Local Search verifies the named anchor only. It is never used as a
    # bulk store source, and an ambiguous result does not silently move it.
    verification = ({"source_status": "no_data"} if historical else
                    (naver_client or NaverLocationClient()).resolve(location.place_name, location.radius_m))
    maps = ({"source_status": "no_data"} if historical else
            (maps_client or NaverMapsClient()).reverse_geocode(location.latitude, location.longitude))
    if maps.get("source_status") == "ok":
        location = replace(location, **{key: value for key, value in maps.items()
                                       if key in ("district", "administrative_dong",
                                                  "administrative_dong_code", "road_address") and value})
    location_verification = {"source_status": verification.get("source_status"),
                             "diagnostics": verification.get("diagnostics", []), "maps": maps}
    population_api = population_provider or (configured_population(location) if location.administrative_dong_code
                                            == HONGDAE_DEFAULT.administrative_dong_code else None)
    population_api = population_api or NoDataLivingPopulationProvider()
    baseline_api = baseline_provider or AdministrativeDongBaselineProvider()
    living_population = population_api.get(date, band, location.administrative_dong)
    content = (_content_no_data(location, date, band) if historical and content_provider is _cached_content
               else content_provider(location, date, band))
    special = special_day_provider(date)
    radius = ({"radius_m": location.radius_m, "source_status": "no_data",
               "reason": "historical_inventory_unavailable"} if historical else
              collect_radius_stores(location.latitude, location.longitude, location.radius_m, store_client))
    if not historical and radius["source_status"] != "ok":
        radius = radius_inventory(location) or radius
    baseline = (baseline_api.get_for_location(location, date)
                if isinstance(baseline_api, AdministrativeDongBaselineProvider)
                else baseline_api.get(location.administrative_dong))
    dynamic = {"living_population": living_population, "content": content, "special_day": special}
    scoring = calculate_hongdae_indicators(dynamic, baseline, radius)
    observed_score_inputs = (living_population.get("population") is not None or bool(content.get("count"))
                             or bool(special.get("is_special_day")) or baseline.get("estimated_sales") is not None
                             or radius.get("source_status") == "ok")
    narrative = _narrative(scoring["opportunity_score"], baseline, radius)
    if not observed_score_inputs:
        narrative["judgement_sentence"] = "점수에 반영할 관측 자료를 확보하지 못했습니다."
        scoring["score_status"] = "no_data"
    else:
        scoring["score_status"] = "provisional"
    source_statuses = [living_population.get("source_status"), content.get("source_status"),
                       special.get("source_status"), radius.get("source_status"), baseline.get("source_status")]
    quality = ("ok" if all(x == "ok" for x in source_statuses)
               else "partial" if any(x in ("ok", "partial") for x in source_statuses) else "no_data")
    payload = {
        "schema_version": "1.0", "location": location.to_dict(), "date": date,
        "time": resolved_time, "time_band": band, "dynamic_signals": dynamic,
        "structural_baseline": {**baseline, "radius_competition": radius},
        "indicators": scoring["indicators"], "scoring": scoring,
        "opportunity_score": scoring["opportunity_score"] if observed_score_inputs else None,
        "decision_tags": ["좌표반경", "날씨미사용"],
        "data_quality": {"status": quality, "source_statuses": source_statuses,
                         "location_verification": location_verification},
        "narrative": narrative,
        "generated_at": now.isoformat(),
    }
    if persist:
        write_snapshot(snapshot_path(date, band, data_dir), payload)
    return payload


def load_hongdae_snapshot(date: str, time_band: str,
                           data_dir: str | Path = "data/hongdae/daily") -> dict | None:
    return read_snapshot(snapshot_path(date, time_band, data_dir))
