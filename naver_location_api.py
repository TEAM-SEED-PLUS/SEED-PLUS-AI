"""Credential-safe NAVER Local Search adapter for resolving an analysis anchor."""
from __future__ import annotations

import os
import re
from typing import Any

import requests

from location_context import LocationContext

NAVER_LOCAL_URL = "https://openapi.naver.com/v1/search/local.json"
MAPS_URL = "https://maps.apigw.ntruss.com"


class NaverMapsClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def _get(self, path, params):
        key = os.getenv("NAVER_MAPS_CLIENT_ID")
        secret = os.getenv("NAVER_MAPS_CLIENT_SECRET")
        if not key or not secret:
            return {"source_status": "no_data", "reason": "missing_maps_credentials"}, {}
        try:
            response = self.session.get(
                MAPS_URL + path, params=params, timeout=10, allow_redirects=False,
                headers={"x-ncp-apigw-api-key-id": key, "x-ncp-apigw-api-key": secret},
            )
            if response.status_code != 200:
                return {"source_status": "failed", "http_status": response.status_code}, {}
            return {"source_status": "ok"}, response.json()
        except (requests.RequestException, ValueError):
            return {"source_status": "failed", "reason": "maps_request_failed"}, {}

    def geocode(self, address):
        status, payload = self._get("/map-geocode/v2/geocode", {"query": address})
        rows = payload.get("addresses", [])
        if len(rows) != 1:
            if status["source_status"] == "ok":
                status.update(source_status="no_data", candidate_count=len(rows))
            return status
        row = rows[0]
        return {**status, "latitude": float(row["y"]), "longitude": float(row["x"]),
                "road_address": row.get("roadAddress")}

    def reverse_geocode(self, latitude, longitude):
        status, payload = self._get("/map-reversegeocode/v2/gc", {
            "request": "coordsToaddr", "coords": f"{longitude},{latitude}",
            "sourcecrs": "epsg:4326", "orders": "admcode,roadaddr", "output": "json",
        })
        rows = {row["name"]: row for row in payload.get("results", [])}
        if not rows:
            if status["source_status"] == "ok":
                status.update(source_status="no_data")
            return status
        admin = rows.get("admcode", {})
        road = rows.get("roadaddr", {})
        region = admin.get("region", {})
        land = road.get("land", {})
        number = "-".join(filter(None, [land.get("number1"), land.get("number2")]))
        address = " ".join(filter(None, [region.get("area1", {}).get("name"),
                                        region.get("area2", {}).get("name"), land.get("name"), number]))
        return {**status, "administrative_dong": region.get("area3", {}).get("name"),
                "administrative_dong_code": admin.get("code", {}).get("id"),
                "district": region.get("area2", {}).get("name"),
                "road_address": address if land.get("name") else None}


def _clean(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


class NaverLocationClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 *, session: requests.Session | None = None, timeout: int = 10) -> None:
        self._client_id = (client_id if client_id is not None else os.getenv("NAVER_LOCAL_CLIENT_ID", "")).strip()
        self._client_secret = (client_secret if client_secret is not None else os.getenv("NAVER_LOCAL_CLIENT_SECRET", "")).strip()
        self._session = session or requests.Session()
        self.timeout = timeout

    def resolve(self, query: str, radius_m: int = 300) -> dict[str, Any]:
        if not self._client_id or not self._client_secret:
            return {"location": None, "source_status": "no_data",
                    "diagnostics": ["naver_local_credentials_missing"]}
        try:
            response = self._session.get(
                NAVER_LOCAL_URL, params={"query": query, "display": 5, "sort": "random"},
                headers={"X-Naver-Client-Id": self._client_id,
                         "X-Naver-Client-Secret": self._client_secret}, timeout=self.timeout,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
        except (requests.RequestException, ValueError, AttributeError) as exc:
            return {"location": None, "source_status": "failed",
                    "diagnostics": [f"naver_local_request_failed:{type(exc).__name__}"]}
        candidates = [x for x in items if isinstance(x, dict) and x.get("mapx") and x.get("mapy")]
        if len(candidates) != 1:
            return {"location": None, "source_status": "no_data",
                    "diagnostics": [f"ambiguous_location_candidates:{len(candidates)}"]}
        item = candidates[0]
        try:
            location = LocationContext(
                place_name=_clean(item.get("title")) or query,
                latitude=float(item["mapy"]) / 10_000_000,
                longitude=float(item["mapx"]) / 10_000_000,
                radius_m=radius_m,
                road_address=_clean(item.get("roadAddress")) or None,
            )
        except (TypeError, ValueError, KeyError):
            return {"location": None, "source_status": "no_data",
                    "diagnostics": ["invalid_location_coordinates"]}
        return {"location": location, "source_status": "ok", "diagnostics": []}
