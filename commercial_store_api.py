"""Research-only client for the SEMAS commercial-store API.

This module deliberately is not connected to the production indicator pipeline.
It exists to make the measured-store competition investigation reproducible.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any, Iterable

import requests


BASE_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2"
SERVICE_KEY_ENV_NAMES = ("COMMERCIAL_STORE_API_KEY", "DATA_GO_KR_SERVICE_KEY", "TOURISM_DATA_API_KEY")
MAX_ROWS_PER_REQUEST = 1000
SOURCE_STATUSES = {"ok", "partial", "no_data", "failed", "fallback"}


class CommercialStoreAPIError(RuntimeError):
    """An API transport or result-code failure with credentials excluded."""


def _dotenv_value(name: str, path: str | Path = ".env") -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'")
    return ""


def load_service_key(dotenv_path: str | Path = ".env") -> str:
    """Load an existing key without placing it in output, cache, or source files."""
    for name in SERVICE_KEY_ENV_NAMES:
        value = os.getenv(name, "").strip() or _dotenv_value(name, dotenv_path)
        if value:
            return value
    raise CommercialStoreAPIError(
        "상가정보 API 키가 없습니다. COMMERCIAL_STORE_API_KEY 또는 DATA_GO_KR_SERVICE_KEY를 설정하세요."
    )


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = body.get("items") or []
    if isinstance(value, dict):
        value = value.get("item") or []
    if isinstance(value, dict):
        value = [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class CommercialStoreClient:
    def __init__(
        self,
        service_key: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 30,
        min_interval_seconds: float = 0.12,
    ) -> None:
        if not service_key:
            raise ValueError("service_key is required")
        self._service_key = service_key
        self._session = session or requests.Session()
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self._last_request_at = 0.0

    def _request(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        wait = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        safe_params = {key: value for key, value in params.items() if value is not None}
        private_params = {"serviceKey": self._service_key, **safe_params}
        try:
            response = self._session.get(
                f"{BASE_URL}/{operation}", params=private_params, timeout=self.timeout
            )
            self._last_request_at = time.monotonic()
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CommercialStoreAPIError(f"{operation} transport failure: {type(exc).__name__}") from exc

        api_response = payload.get("response", payload) if isinstance(payload, dict) else {}
        header = api_response.get("header") or {}
        body = api_response.get("body") or {}
        code = str(header.get("resultCode") or "")
        if code == "03":
            return {"status": "no_data", "total_count": 0, "items": [], "page_no": params.get("pageNo", 1)}
        if code != "00":
            raise CommercialStoreAPIError(
                f"{operation} API failure: resultCode={code or 'unknown'}, resultMsg={header.get('resultMsg') or 'unknown'}"
            )
        return {
            "status": "ok",
            "total_count": int(body.get("totalCount") or 0),
            "items": _items(body),
            "page_no": int(body.get("pageNo") or params.get("pageNo") or 1),
            "num_of_rows": int(body.get("numOfRows") or params.get("numOfRows") or 0),
        }

    def stores_in_dong(
        self, div_id: str, key: str, *, page_no: int = 1, rows: int = MAX_ROWS_PER_REQUEST,
        inds_lcls_cd: str | None = None, inds_mcls_cd: str | None = None,
        inds_scls_cd: str | None = None,
    ) -> dict[str, Any]:
        return self._request("storeListInDong", {
            "divId": div_id, "key": key, "pageNo": page_no,
            "numOfRows": min(MAX_ROWS_PER_REQUEST, max(1, rows)), "type": "json",
            "indsLclsCd": inds_lcls_cd, "indsMclsCd": inds_mcls_cd,
            "indsSclsCd": inds_scls_cd,
        })

    def stores_in_radius(
        self, latitude: float, longitude: float, radius_m: int, *, page_no: int = 1,
        rows: int = MAX_ROWS_PER_REQUEST,
    ) -> dict[str, Any]:
        return self._request("storeListInRadius", {
            "cy": latitude, "cx": longitude, "radius": radius_m, "pageNo": page_no,
            "numOfRows": min(MAX_ROWS_PER_REQUEST, max(1, rows)), "type": "json",
        })

    def stores_in_industry(
        self, div_id: str, key: str, *, page_no: int = 1, rows: int = MAX_ROWS_PER_REQUEST
    ) -> dict[str, Any]:
        return self._request("storeListInUpjong", {
            "divId": div_id, "key": key, "pageNo": page_no,
            "numOfRows": min(MAX_ROWS_PER_REQUEST, max(1, rows)), "type": "json",
        })

    def fetch_all_in_dong(self, div_id: str, key: str) -> dict[str, Any]:
        first = self.stores_in_dong(div_id, key)
        if first["status"] == "no_data":
            return {**first, "unique_count": 0, "duplicate_count": 0, "pages_requested": 1}
        total = first["total_count"]
        pages = max(1, math.ceil(total / MAX_ROWS_PER_REQUEST))
        records = list(first["items"])
        failed_pages: list[int] = []
        for page_no in range(2, pages + 1):
            try:
                records.extend(self.stores_in_dong(div_id, key, page_no=page_no)["items"])
            except CommercialStoreAPIError:
                failed_pages.append(page_no)
        unique = dedupe_stores(records)
        status = "partial" if failed_pages or len(unique) < total else "ok"
        return {
            "status": status,
            "total_count": total,
            "items": unique,
            "unique_count": len(unique),
            "duplicate_count": len(records) - len(unique),
            "pages_requested": pages,
            "failed_pages": failed_pages,
        }


def dedupe_stores(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the API's stable store identifier; never guess from name and address."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        store_id = str(record.get("bizesId") or "").strip()
        if store_id and store_id in seen:
            continue
        if store_id:
            seen.add(store_id)
        result.append(record)
    return result
