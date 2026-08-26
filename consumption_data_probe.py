"""Probe clients for Seoul estimated sales and OA-22385 realtime commerce.

Research-only: these sources are not connected to production spending scoring.
"""

from __future__ import annotations

from typing import Any

import requests


SEOUL_OPENAPI_BASE = "http://openapi.seoul.go.kr:8088"
ESTIMATED_SALES_SERVICE = "VwsmSignguSelngW"
DISTRICT_STORES_SERVICE = "VwsmSignguStorW"
REALTIME_COMMERCE_SERVICE = "citydata_cmrcl"


class ConsumptionDataError(RuntimeError):
    pass


def _rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    rows = block.get("row") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


class SeoulConsumptionClient:
    def __init__(self, api_key: str, *, session: requests.Session | None = None, timeout: int = 30) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self.timeout = timeout

    def _get_json(self, service: str, suffix: str) -> dict[str, Any]:
        try:
            response = self._session.get(
                f"{SEOUL_OPENAPI_BASE}/{self._api_key}/json/{service}/{suffix}", timeout=self.timeout
            )
            response.raise_for_status()
            value = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ConsumptionDataError(f"{service} transport failure: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise ConsumptionDataError(f"{service} malformed response")
        return value

    def estimated_sales_page(self, start: int = 1, end: int = 1000) -> dict[str, Any]:
        payload = self._get_json(ESTIMATED_SALES_SERVICE, f"{start}/{end}/")
        block = payload.get(ESTIMATED_SALES_SERVICE)
        if not isinstance(block, dict):
            result = payload.get("RESULT") or {}
            code = str(result.get("CODE") or "")
            if code == "INFO-200":
                return {"source_status": "no_data", "total_count": 0, "items": []}
            raise ConsumptionDataError(f"{ESTIMATED_SALES_SERVICE} malformed response")
        result = block.get("RESULT") or {}
        code = str(result.get("CODE") or "")
        if code == "INFO-200":
            return {"source_status": "no_data", "total_count": 0, "items": []}
        if code != "INFO-000":
            raise ConsumptionDataError(f"{ESTIMATED_SALES_SERVICE} API failure: {code or 'unknown'}")
        return {"source_status": "ok", "total_count": int(block.get("list_total_count") or 0), "items": _rows(block)}

    def estimated_sales_all(self, page_size: int = 1000) -> dict[str, Any]:
        first = self.estimated_sales_page(1, page_size)
        if first["source_status"] == "no_data":
            return {**first, "api_call_count": 1, "duplicate_grain_count": 0}
        records = list(first["items"])
        calls = 1
        for start in range(page_size + 1, first["total_count"] + 1, page_size):
            records.extend(self.estimated_sales_page(start, min(start + page_size - 1, first["total_count"]))["items"])
            calls += 1
        grain = [(str(row.get("STDR_YYQU_CD")), str(row.get("SIGNGU_CD")), str(row.get("SVC_INDUTY_CD"))) for row in records]
        return {
            "source_status": "ok", "total_count": first["total_count"], "items": records,
            "api_call_count": calls, "duplicate_grain_count": len(grain) - len(set(grain)),
        }

    def district_stores_page(self, start: int = 1, end: int = 1000) -> dict[str, Any]:
        payload = self._get_json(DISTRICT_STORES_SERVICE, f"{start}/{end}/")
        block = payload.get(DISTRICT_STORES_SERVICE)
        if not isinstance(block, dict):
            result = payload.get("RESULT") or {}
            if str(result.get("CODE") or "") == "INFO-200":
                return {"source_status": "no_data", "total_count": 0, "items": []}
            raise ConsumptionDataError(f"{DISTRICT_STORES_SERVICE} malformed response")
        result = block.get("RESULT") or {}
        code = str(result.get("CODE") or "")
        if code == "INFO-200":
            return {"source_status": "no_data", "total_count": 0, "items": []}
        if code != "INFO-000":
            raise ConsumptionDataError(f"{DISTRICT_STORES_SERVICE} API failure: {code or 'unknown'}")
        return {"source_status": "ok", "total_count": int(block.get("list_total_count") or 0),
                "items": _rows(block)}

    def realtime_commerce(self, area: str) -> dict[str, Any]:
        payload = self._get_json(REALTIME_COMMERCE_SERVICE, f"1/5/{area}")
        result = payload.get("RESULT") or {}
        code = str(result.get("resultCode") or result.get("CODE") or "")
        if code in {"INFO-200", "INFO-300"}:
            return {"source_status": "no_data", "area_cd": area, "commercial": None}
        if code != "INFO-000":
            raise ConsumptionDataError(f"{REALTIME_COMMERCE_SERVICE} API failure: {code or 'unknown'}")
        commercial = payload.get("LIVE_CMRCL_STTS")
        if not isinstance(commercial, dict):
            raise ConsumptionDataError(f"{REALTIME_COMMERCE_SERVICE} malformed response")
        return {
            "source_status": "ok", "area_cd": payload.get("AREA_CD"),
            "area_nm": payload.get("AREA_NM"), "commercial": commercial,
        }
