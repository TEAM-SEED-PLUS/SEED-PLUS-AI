"""Provider boundaries for Hongdae radius, quarterly baseline, and 250m population."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import asin, ceil, cos, pi, radians, sin, sqrt
from pathlib import Path
from typing import Any, Protocol

from commercial_store_api import CommercialStoreAPIError, CommercialStoreClient, dedupe_stores, load_service_key
from snapshot_cache import read_snapshot
from common import SEOUL_TZ


class LivingPopulationProvider(Protocol):
    def get(self, date: str, time_band: str, administrative_dong: str | None) -> dict[str, Any]: ...


class NoDataLivingPopulationProvider:
    def get(self, date: str, time_band: str, administrative_dong: str | None) -> dict[str, Any]:
        return {"date": date, "time_band": time_band, "administrative_dong": administrative_dong,
                "population": None, "source_status": "no_data", "source_time": None}


class AdministrativeDongBaselineProvider:
    def __init__(self, path: str | Path = "data/hongdae/baseline/latest.json") -> None:
        self.path = Path(path)

    def get_for_location(self, location, date):
        from hongdae_data import quarterly_baseline
        return quarterly_baseline(location, date) or self.get(location.administrative_dong)

    def get(self, administrative_dong: str | None) -> dict[str, Any]:
        payload = read_snapshot(self.path) or {}
        row = (payload.get("administrative_dongs") or {}).get(administrative_dong or "")
        if not isinstance(row, dict):
            return {"estimated_sales": None, "total_store_count": None, "industry_counts": {},
                    "opening_rate": None, "opening_store_count": None, "closure_rate": None,
                    "closure_store_count": None, "source_status": "no_data",
                    "metadata": {"source_period": None, "grain": "administrative_dong",
                                 "baseline_type": "quarterly"}}
        return {**row, "source_status": row.get("source_status", "ok"),
                "metadata": {"source_period": payload.get("source_period"),
                             "grain": "administrative_dong", "baseline_type": "quarterly"}}


def collect_radius_stores(latitude: float, longitude: float, radius_m: int,
                          client: CommercialStoreClient | None = None) -> dict[str, Any]:
    try:
        api = client or CommercialStoreClient(load_service_key())
        result = api.stores_in_radius(latitude, longitude, radius_m)
    except (CommercialStoreAPIError, ValueError):
        return {"radius_m": radius_m, "total_store_count": None, "industry_counts": {},
                "franchise_count": None, "source_status": "failed"}
    records = list(result.get("items", []))
    total = result.get("total_count", len(records))
    pages = ceil(total / max(1, result.get("num_of_rows") or 1000))
    failed_pages = []
    for page in range(2, pages + 1):
        try:
            batch = api.stores_in_radius(latitude, longitude, radius_m, page_no=page)
            records.extend(batch["items"])
            if batch["status"] != "ok":
                failed_pages.append(page)
        except CommercialStoreAPIError:
            failed_pages.append(page)
    stores = dedupe_stores(records)
    industries = Counter(str(x.get("indsLclsNm") or x.get("indsMclsNm") or "미분류") for x in stores)
    items = []
    for row in stores:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        a = sin(radians(lat - latitude) / 2)**2 + (
            cos(radians(latitude)) * cos(radians(lat)) * sin(radians(lon - longitude) / 2)**2)
        items.append({"id": row.get("bizesId"), "latitude": lat, "longitude": lon,
                      "industry_code": row.get("indsSclsCd"),
                      "distance_m": round(12_742_000 * asin(sqrt(min(a, 1))), 1)})
    status = "partial" if failed_pages or len(stores) < total else result.get("status", "ok")
    return {"radius_m": radius_m, "total_store_count": len(stores) if status != "no_data" else None,
            "industry_counts": dict(industries), "franchise_count": None,
            "same_industry_counts": dict(Counter(row["indsSclsCd"] for row in stores if row.get("indsSclsCd"))),
            "stores_per_km2": round(len(stores) / (pi * (radius_m / 1000)**2), 1) if status == "ok" else None,
            "stores": items, "missing_coordinates": len(stores) - len(items),
            "missing_store_ids": sum(not row.get("bizesId") for row in stores),
            "duplicate_count": len(records) - len(stores), "failed_pages": failed_pages,
            "collected_at": datetime.now(SEOUL_TZ).isoformat(), "source_updated_at": None,
            "source_status": status}
