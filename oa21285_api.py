"""서울 실시간 도시데이터 OA-21285의 독립 수집 client.

이 모듈의 결과는 아직 production footfall/indicator 입력이 아니다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from common import DEFAULT_SEOUL_KEY, SEOUL_OPENAPI_BASE, SEOUL_TZ
except ImportError:  # pragma: no cover
    from .common import DEFAULT_SEOUL_KEY, SEOUL_OPENAPI_BASE, SEOUL_TZ


SERVICE_NAME = "citydata"
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_FACTOR = 0.3


def _integer(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _result_from_json(payload: dict[str, Any]) -> tuple[str, str]:
    result = payload.get("RESULT", {})
    if not isinstance(result, dict):
        result = {}
    code = result.get("RESULT.CODE") or result.get("CODE") or payload.get("RESULT.CODE") or ""
    message = result.get("RESULT.MESSAGE") or result.get("MESSAGE") or payload.get("RESULT.MESSAGE") or ""
    return str(code).strip(), str(message).strip()


def _result_from_xml(text: str) -> tuple[str, str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "INVALID_RESPONSE", "JSON 또는 XML 응답을 해석하지 못했습니다."
    code = root.findtext(".//CODE") or root.findtext(".//RESULT.CODE") or ""
    message = root.findtext(".//MESSAGE") or root.findtext(".//RESULT.MESSAGE") or ""
    return code.strip(), message.strip()


def _failed(area: str, code: str, message: str, *, http_status: int | None = None) -> dict[str, Any]:
    return {
        "area_cd": area if area.upper().startswith("POI") else None,
        "area_nm": None if area.upper().startswith("POI") else area,
        "population": {"min": None, "max": None},
        "congestion": {"level": "", "message": ""},
        "population_time": "",
        "replace_yn": "",
        "forecast_yn": "",
        "forecast": [],
        "source_status": "failed",
        "result_code": code,
        "result_message": message,
        "http_status": http_status,
    }


def parse_oa21285_response(
    response: requests.Response,
    requested_area: str,
    *,
    reference_place: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HTTP 응답을 current/forecast가 분리된 공통 raw schema로 변환한다."""
    http_status = response.status_code
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        return _failed(requested_area, "HTTP_ERROR", type(exc).__name__, http_status=http_status)

    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError):
        code, message = _result_from_xml(response.text)
        return _failed(requested_area, code or "INVALID_RESPONSE", message, http_status=http_status)

    if not isinstance(payload, dict):
        return _failed(requested_area, "INVALID_RESPONSE", "JSON 최상위가 object가 아닙니다.", http_status=http_status)
    code, message = _result_from_json(payload)
    if code != "INFO-000":
        return _failed(requested_area, code or "INVALID_RESPONSE", message, http_status=http_status)

    citydata = payload.get("CITYDATA")
    if isinstance(citydata, list):
        citydata = citydata[0] if citydata and isinstance(citydata[0], dict) else {}
    if not isinstance(citydata, dict):
        citydata = {}
    live = _as_list(citydata.get("LIVE_PPLTN_STTS"))
    live = [item for item in live if isinstance(item, dict)]
    area_cd = str(citydata.get("AREA_CD") or requested_area if requested_area.upper().startswith("POI") else citydata.get("AREA_CD") or "")
    area_nm = str(citydata.get("AREA_NM") or ("" if requested_area.upper().startswith("POI") else requested_area))
    if not live:
        return {
            **_failed(requested_area, code, message, http_status=http_status),
            "area_cd": area_cd or None,
            "area_nm": area_nm or None,
            "source_status": "no_data",
        }

    current = live[0]
    forecasts = []
    for item in _as_list(current.get("FCST_PPLTN")):
        if not isinstance(item, dict):
            continue
        forecasts.append({
            "time": str(item.get("FCST_TIME") or ""),
            "population_min": _integer(item.get("FCST_PPLTN_MIN")),
            "population_max": _integer(item.get("FCST_PPLTN_MAX")),
            "congestion_level": str(item.get("FCST_CONGEST_LVL") or ""),
        })
    area_cd = str(current.get("AREA_CD") or citydata.get("AREA_CD") or area_cd)
    area_nm = str(current.get("AREA_NM") or citydata.get("AREA_NM") or area_nm)
    reference_name = str((reference_place or {}).get("area_nm") or (reference_place or {}).get("AREA_NM") or "")
    return {
        "area_cd": area_cd,
        "area_nm": area_nm,
        "population": {
            "min": _integer(current.get("AREA_PPLTN_MIN")),
            "max": _integer(current.get("AREA_PPLTN_MAX")),
        },
        "congestion": {
            "level": str(current.get("AREA_CONGEST_LVL") or ""),
            "message": str(current.get("AREA_CONGEST_MSG") or ""),
        },
        "population_time": str(current.get("PPLTN_TIME") or ""),
        "replace_yn": str(current.get("REPLACE_YN") or ""),
        "forecast_yn": str(current.get("FCST_YN") or ""),
        "forecast": forecasts,
        "source_status": "ok",
        "result_code": code,
        "result_message": message,
        "http_status": http_status,
        "diagnostics": {
            "reference_name": reference_name or None,
            "live_name": area_nm or None,
            "name_mismatch": bool(reference_name and area_nm and reference_name != area_nm),
        },
    }


class OA21285Client:
    def __init__(
        self,
        api_key: str = DEFAULT_SEOUL_KEY,
        *,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        if session is None:
            retry = Retry(
                total=max(0, max_retries), connect=max(0, max_retries), read=max(0, max_retries),
                backoff_factor=max(0.0, backoff_factor), status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}), raise_on_status=False,
            )
            self.session.mount("http://", HTTPAdapter(max_retries=retry))
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get_place(
        self,
        area_cd_or_nm: str,
        *,
        reference_place: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        area = str(area_cd_or_nm or "").strip()
        if not area:
            raise ValueError("AREA_CD 또는 AREA_NM이 필요합니다.")
        url = f"{SEOUL_OPENAPI_BASE}/{self.api_key}/json/{SERVICE_NAME}/1/5/{quote(area)}"
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            return _failed(area, "TRANSPORT_ERROR", type(exc).__name__)
        return parse_oa21285_response(response, area, reference_place=reference_place)


def collected_record(result: dict[str, Any], received_at: datetime | None = None) -> dict[str, Any]:
    """API key 없이 cache에 저장할 envelope를 만든다."""
    timestamp = (received_at or datetime.now(SEOUL_TZ)).isoformat(timespec="seconds")
    return {
        "area_cd": result.get("area_cd"),
        "area_nm": result.get("area_nm"),
        "received_at": timestamp,
        "population_time": result.get("population_time"),
        "source_status": result.get("source_status"),
        "current": {
            "population": result.get("population", {}),
            "congestion": result.get("congestion", {}),
            "population_time": result.get("population_time"),
            "replace_yn": result.get("replace_yn"),
        },
        "forecast_yn": result.get("forecast_yn"),
        "forecast": result.get("forecast", []),
        "result_code": result.get("result_code"),
        "result_message": result.get("result_message"),
        "http_status": result.get("http_status"),
        "diagnostics": result.get("diagnostics", {}),
    }
