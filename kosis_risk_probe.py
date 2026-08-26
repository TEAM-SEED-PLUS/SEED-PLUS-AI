"""Research-only KOSIS enterprise-death client and normalizer.

This module is intentionally disconnected from production risk scoring.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from typing import Any

import requests


BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
API_KEY_ENV = "KOSIS_API_KEY"


class KosisAPIError(RuntimeError):
    """A sanitized KOSIS transport or response error."""


def load_api_key() -> str:
    key = os.getenv(API_KEY_ENV, "").strip()
    if not key:
        raise KosisAPIError(f"{API_KEY_ENV} is not configured")
    return key


def parse_year(period: Any) -> int | None:
    text = str(period or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        if 1900 <= year <= 2200:
            return year
    return None


def _dimension(row: dict[str, Any], prefix: str) -> dict[str, str] | None:
    code = str(row.get(prefix) or "").strip()
    name = str(row.get(f"{prefix}_NM") or "").strip()
    return {"code": code, "name": name} if code or name else None


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise KosisAPIError("malformed row")
    dimensions = [value for index in range(1, 9) if (value := _dimension(row, f"C{index}"))]
    return {
        "organization_id": str(row.get("ORG_ID") or ""),
        "table_id": str(row.get("TBL_ID") or ""),
        "table_name": str(row.get("TBL_NM") or ""),
        "period": str(row.get("PRD_DE") or ""),
        "source_year": parse_year(row.get("PRD_DE")),
        "item_id": str(row.get("ITM_ID") or ""),
        "item_name": str(row.get("ITM_NM") or ""),
        "unit_id": str(row.get("UNIT_ID") or ""),
        "unit_name": str(row.get("UNIT_NM") or ""),
        "value": row.get("DT"),
        "dimensions": dimensions,
        "region": dimensions[0] if dimensions else None,
        "industry": dimensions[1] if len(dimensions) > 1 else None,
    }


def grain_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "") for key in (
        "TBL_ID", "PRD_DE", "ITM_ID", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"
    ))


def dedupe_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for row in rows:
        key = grain_key(row)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(row)
    return unique, duplicates


class KosisClient:
    def __init__(self, api_key: str, *, session: requests.Session | None = None,
                 timeout: int = 30, retries: int = 2, retry_delay: float = 0.1) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_delay = max(0.0, retry_delay)

    def statistics(self, *, org_id: str, table_id: str, item_id: str,
                   object_ids: dict[str, str], start_year: int, end_year: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "method": "getList", "apiKey": self._api_key, "format": "json", "jsonVD": "Y",
            "orgId": org_id, "tblId": table_id, "itmId": item_id, "prdSe": "Y",
            "startPrdDe": start_year, "endPrdDe": end_year,
            **object_ids,
        }
        payload: Any = None
        last_error = "unknown"
        for attempt in range(self.retries + 1):
            try:
                response = self._session.get(BASE_URL, params=params, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = type(exc).__name__
                if attempt < self.retries:
                    time.sleep(self.retry_delay)
        else:
            raise KosisAPIError(f"KOSIS transport failure: {last_error}")

        if isinstance(payload, dict):
            code = str(payload.get("err") or payload.get("errorCode") or payload.get("resultCode") or "unknown")
            raise KosisAPIError(f"KOSIS API failure: code={code}")
        if not isinstance(payload, list):
            raise KosisAPIError("KOSIS malformed response")
        rows = [row for row in payload if isinstance(row, dict)]
        if len(rows) != len(payload):
            raise KosisAPIError("KOSIS malformed response row")
        if not rows:
            return {"source_status": "no_data", "items": [], "duplicate_grain_count": 0}
        unique, duplicate_count = dedupe_rows(rows)
        return {
            "source_status": "ok",
            "items": [normalize_row(row) for row in unique],
            "duplicate_grain_count": duplicate_count,
        }
