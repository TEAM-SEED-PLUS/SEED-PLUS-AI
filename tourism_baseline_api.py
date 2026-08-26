"""한국관광공사 관광데이터랩 지역별 관광지표 raw 수집 계층."""

from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

import requests

try:
    from common import (
        TOURISM_DATA_SEOUL_AREA_CODE,
        TOURISM_DATA_SEOUL_SIGNGU,
        normalize_district,
    )
except ImportError:  # pragma: no cover
    from .common import (
        TOURISM_DATA_SEOUL_AREA_CODE,
        TOURISM_DATA_SEOUL_SIGNGU,
        normalize_district,
    )


SERVICE_BASE_URLS = {
    "AreaTarDemDsService": "https://apis.data.go.kr/B551011/AreaTarDemDsService",
    "AreaTarDivService": "https://apis.data.go.kr/B551011/AreaTarDivService",
    "AreaTarResDemService": "https://apis.data.go.kr/B551011/AreaTarResDemService",
}

TOURISM_INDICATOR_CODES = {
    "stay_intensity": ["2101", "2102", "2103", "2104", "2105"],
    "spending_intensity": ["2201", "2202", "2203"],
    "tourist_diversity": [f"310{i}" for i in range(1, 8)],
    "spending_diversity": [f"320{i}" for i in range(1, 8)],
    "international_diversity": ["3301", "3302", "3303"],
    "service_demand": [f"11{i:02d}" for i in range(1, 13)],
    "cultural_resource_demand": [f"120{i}" for i in range(1, 6)],
}

# 공식 v4.0 가이드에서는 선택(0)이지만, live 확인 결과 생략 시 0000/0건이므로
# 통합 수집은 지원 세부 코드를 반복 호출한다. 개별 request에서는 생략도 허용한다.
OPERATION_SPECS = {
    "areaTarSjrnDsList": ("AreaTarDemDsService", "tarSjrnDsIxCd", "stay_intensity"),
    "areaTarExpDsList": ("AreaTarDemDsService", "tarExpDsIxCd", "spending_intensity"),
    "areaTouDivList": ("AreaTarDivService", "touDivIxCd", "tourist_diversity"),
    "areaExpDivList": ("AreaTarDivService", "expDivIxCd", "spending_diversity"),
    "areaIntlDivList": ("AreaTarDivService", "intlDivIxCd", "international_diversity"),
    "areaTarSvcDemList": ("AreaTarResDemService", "tarSvcDemIxCd", "service_demand"),
    "areaCulResDemList": ("AreaTarResDemService", "culResDemIxCd", "cultural_resource_demand"),
}
OPERATION_SPECS_BY_METRIC = {metric_name: operation for operation, (_, _, metric_name) in OPERATION_SPECS.items()}

INDICATOR_FIELD_SPECS = {
    "stay_intensity": ("tarSjrnDsIxCd", "tarSjrnDsIxNm", "tarSjrnDsIxVal"),
    "spending_intensity": ("tarExpDsIxCd", "tarExpDsIxNm", "tarExpDsIxVal"),
    "tourist_diversity": ("touDivIxCd", "touDivIxNm", "touDivIxVal"),
    "spending_diversity": ("expDivIxCd", "expDivIxNm", "expDivIxVal"),
    "international_diversity": ("intlDivIxCd", "intlDivIxNm", "intlDivIxVal"),
    "service_demand": ("tarSvcDemIxCd", "tarSvcDemIxNm", "tarSvcDemIxVal"),
    "cultural_resource_demand": ("culResDemIxCd", "culResDemIxNm", "culResDemIxVal"),
}

TOURISM_CONFIGURATION_PENDING: dict[str, str] = {}

SUCCESS_CODES = {"00", "0000"}
NO_DATA_CODES = {"03", "0003"}
TOURISM_DATA_API_KEY_ENV = "TOURISM_DATA_API_KEY"


class TourismBaselineClient:
    def __init__(
        self,
        service_key: str | None = None,
        *,
        timeout: float = 15,
        mobile_os: str = "ETC",
        mobile_app: str = "seed_weatherfeed",
        session: requests.Session | None = None,
    ) -> None:
        self.service_key = _resolve_service_key(service_key)
        self.timeout = timeout
        self.mobile_os = mobile_os
        self.mobile_app = mobile_app
        self.session = session or requests.Session()

    def request(
        self,
        operation: str,
        *,
        base_ym: str,
        area_cd: str,
        signgu_cd: str | None,
        indicator_code: str | None = None,
        page_no: int = 1,
        num_of_rows: int = 100,
    ) -> dict[str, Any]:
        if operation not in OPERATION_SPECS:
            raise ValueError(f"지원하지 않는 operation입니다: {operation}")
        service_name, indicator_param, metric_name = OPERATION_SPECS[operation]
        params = {
            "serviceKey": self.service_key,
            "MobileOS": self.mobile_os,
            "MobileApp": self.mobile_app,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "baseYm": _validate_base_ym(base_ym),
            "areaCd": str(area_cd),
            "_type": "json",
        }
        if signgu_cd is not None:
            params["signguCd"] = str(signgu_cd)
        if indicator_code is not None:
            code = str(indicator_code)
            if code not in TOURISM_INDICATOR_CODES[metric_name]:
                raise ValueError(f"지원하지 않는 {metric_name} 지표코드입니다: {code}")
            params[indicator_param] = code
        public_params = {key: value for key, value in params.items() if key != "serviceKey"}
        url = f"{SERVICE_BASE_URLS[service_name]}/{operation}"

        response = None
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None) or getattr(response, "status_code", None)
            result_code, result_message = _extract_http_error_details(response)
            reason = f"HTTP 오류{f' ({status_code})' if status_code else ''}"
            return _failed_result(
                service_name, operation, public_params, reason,
                result_code=result_code, result_message=result_message, http_status=status_code,
            )
        except requests.Timeout:
            return _failed_result(service_name, operation, public_params, "요청 시간 초과")
        except requests.RequestException as exc:
            return _failed_result(service_name, operation, public_params, f"HTTP 요청 실패 ({type(exc).__name__})")
        except ValueError:
            return _failed_result(service_name, operation, public_params, "JSON 응답 파싱 실패")

        return _parse_response(
            payload, service_name, operation, public_params,
            http_status=getattr(response, "status_code", None),
        )

    def request_all_pages(
        self,
        operation: str,
        *,
        base_ym: str,
        area_cd: str,
        signgu_cd: str | None = None,
        indicator_code: str | None = None,
        num_of_rows: int = 1000,
    ) -> dict[str, Any]:
        """totalCount를 만족할 때까지 page를 조회하고 중복 raw row를 제거한다."""
        page_no = 1
        api_call_count = 0
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        page_results: list[dict[str, Any]] = []
        total_count: int | None = None

        while True:
            result = self.request(
                operation,
                base_ym=base_ym,
                area_cd=area_cd,
                signgu_cd=signgu_cd,
                indicator_code=indicator_code,
                page_no=page_no,
                num_of_rows=num_of_rows,
            )
            api_call_count += 1
            page_results.append(result)
            if result.get("source_status", {}).get("status") == "failed":
                result["api_call_count"] = api_call_count
                result["page_count"] = len(page_results)
                return result

            for item in result.get("items", []):
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    items.append(item)

            try:
                total_count = int(result.get("total_count"))
            except (TypeError, ValueError):
                total_count = None
            if total_count is None or len(items) >= total_count or not result.get("items"):
                break
            page_no += 1

        source_status = "ok" if items else "no_data"
        return {
            "source_status": {
                "status": source_status,
                "service": OPERATION_SPECS[operation][0],
                "operation": operation,
            },
            "result_code": page_results[-1].get("result_code") if page_results else None,
            "result_message": page_results[-1].get("result_message") if page_results else None,
            "count": len(items),
            "items": items,
            "total_count": total_count,
            "api_call_count": api_call_count,
            "page_count": len(page_results),
        }


def _resolve_service_key(service_key: str | None) -> str:
    value = str(service_key or "").strip()
    if not value:
        value = os.getenv(TOURISM_DATA_API_KEY_ENV, "").strip()
    if not value:
        value = _read_project_dotenv_value(TOURISM_DATA_API_KEY_ENV)
    if not value:
        raise ValueError(
            "관광데이터 API 키가 없습니다. 프로젝트 .env 또는 프로세스 환경에 "
            "TOURISM_DATA_API_KEY를 설정하세요."
        )
    return unquote(value)


def _read_project_dotenv_value(name: str) -> str:
    """외부 의존성 없이 프로젝트 루트 .env에서 지정한 키 하나만 읽는다."""
    path = Path(__file__).resolve().parent / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{name}="
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def _extract_http_error_details(response: Any) -> tuple[str, str]:
    """HTTP 오류 body에서 인증키나 요청 URL 없이 코드/메시지만 추출한다."""
    if response is None:
        return "", ""
    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        payload = None
    if isinstance(payload, dict):
        response_obj = payload.get("response", payload)
        header = response_obj.get("header", {}) if isinstance(response_obj, dict) else {}
        if isinstance(header, dict):
            return str(header.get("resultCode", "")).strip(), str(header.get("resultMsg", "")).strip()
    text = getattr(response, "text", "")
    if not isinstance(text, str) or not text.strip():
        return "", ""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return "", ""
    code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode") or ""
    message = (
        root.findtext(".//returnAuthMsg")
        or root.findtext(".//resultMsg")
        or root.findtext(".//errMsg")
        or ""
    )
    return code.strip(), message.strip()


def _validate_base_ym(base_ym: str) -> str:
    value = str(base_ym).strip().replace("-", "")
    if len(value) != 6 or not value.isdigit():
        raise ValueError("base_ym은 YYYYMM 형식이어야 합니다.")
    datetime.strptime(value, "%Y%m")
    return value


def _status(status: str, service: str, operation: str, reason: str = "") -> dict[str, str]:
    result = {"status": status, "service": service, "operation": operation}
    if reason:
        result["reason"] = reason
    return result


def _failed_result(
    service: str, operation: str, request_params: dict[str, Any], reason: str,
    *, result_code: str = "", result_message: str = "", http_status: int | None = None,
) -> dict[str, Any]:
    result = {
        "source_status": _status("failed", service, operation, reason),
        "request_params": request_params,
        "count": 0,
        "items": [],
    }
    if result_code:
        result["result_code"] = result_code
    if result_message:
        result["result_message"] = result_message
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _parse_response(
    payload: Any,
    service: str,
    operation: str,
    request_params: dict[str, Any],
    *,
    http_status: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed_result(service, operation, request_params, "JSON 최상위 응답이 object가 아닙니다.")
    response_obj = payload.get("response")
    if not isinstance(response_obj, dict):
        return _failed_result(service, operation, request_params, "response object가 없습니다.")
    header = response_obj.get("header")
    if not isinstance(header, dict):
        return _failed_result(service, operation, request_params, "response.header object가 없습니다.")

    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()
    if code in NO_DATA_CODES:
        result = {
            "source_status": _status("no_data", service, operation, message or "NODATA_ERROR"),
            "request_params": request_params,
            "result_code": code,
            "count": 0,
            "items": [],
        }
        if http_status is not None:
            result["http_status"] = http_status
        return result
    if code not in SUCCESS_CODES:
        reason = f"TourAPI 오류: {code or 'missing'} / {message or 'unknown'}"
        return _failed_result(service, operation, request_params, reason, result_code=code)

    body = response_obj.get("body")
    if not isinstance(body, dict):
        body = {}
    items_obj = body.get("items")
    if isinstance(items_obj, dict):
        raw_items = items_obj.get("item", [])
    elif isinstance(items_obj, list):
        raw_items = items_obj
    else:
        raw_items = []
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list):
        items = [item for item in raw_items if isinstance(item, dict)]
    else:
        items = []

    result_status = "ok" if items else "no_data"
    result = {
        "source_status": _status(result_status, service, operation),
        "request_params": request_params,
        "result_code": code,
        "result_message": message,
        "count": len(items),
        "items": items,
        "page_no": body.get("pageNo"),
        "num_of_rows": body.get("numOfRows"),
        "total_count": body.get("totalCount"),
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _call_operation(
    operation: str,
    district: str,
    base_ym: str,
    service_key: str | None,
    indicator_code: str | None,
    *,
    timeout: float = 15,
    client: TourismBaselineClient | None = None,
) -> dict[str, Any]:
    district_ko, _ = normalize_district(district)
    signgu_cd = TOURISM_DATA_SEOUL_SIGNGU[district_ko]
    api = client or TourismBaselineClient(service_key, timeout=timeout)
    return api.request(
        operation,
        base_ym=base_ym,
        area_cd=TOURISM_DATA_SEOUL_AREA_CODE,
        signgu_cd=signgu_cd,
        indicator_code=indicator_code,
    )


def get_stay_intensity(district: str, base_ym: str, service_key: str | None = None, tar_sjrn_ds_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaTarSjrnDsList", district, base_ym, service_key, tar_sjrn_ds_ix_cd, **kwargs)


def get_spending_intensity(district: str, base_ym: str, service_key: str | None = None, tar_exp_ds_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaTarExpDsList", district, base_ym, service_key, tar_exp_ds_ix_cd, **kwargs)


def get_tourist_diversity(district: str, base_ym: str, service_key: str | None = None, tou_div_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaTouDivList", district, base_ym, service_key, tou_div_ix_cd, **kwargs)


def get_spending_diversity(district: str, base_ym: str, service_key: str | None = None, exp_div_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaExpDivList", district, base_ym, service_key, exp_div_ix_cd, **kwargs)


def get_international_diversity(district: str, base_ym: str, service_key: str | None = None, intl_div_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaIntlDivList", district, base_ym, service_key, intl_div_ix_cd, **kwargs)


def get_service_demand(district: str, base_ym: str, service_key: str | None = None, tar_svc_dem_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaTarSvcDemList", district, base_ym, service_key, tar_svc_dem_ix_cd, **kwargs)


def get_cultural_resource_demand(district: str, base_ym: str, service_key: str | None = None, cul_res_dem_ix_cd: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _call_operation("areaCulResDemList", district, base_ym, service_key, cul_res_dem_ix_cd, **kwargs)


def get_tourism_baseline_raw(
    district: str,
    base_ym: str,
    service_key: str | None = None,
    *,
    timeout: float = 15,
    indicator_codes: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    district_ko, _ = normalize_district(district)
    normalized_ym = _validate_base_ym(base_ym)
    client = TourismBaselineClient(service_key, timeout=timeout)
    calls = {
        "stay_intensity": get_stay_intensity,
        "spending_intensity": get_spending_intensity,
        "tourist_diversity": get_tourist_diversity,
        "spending_diversity": get_spending_diversity,
        "international_diversity": get_international_diversity,
        "service_demand": get_service_demand,
        "cultural_resource_demand": get_cultural_resource_demand,
    }
    effective_indicator_codes = TOURISM_INDICATOR_CODES if indicator_codes is None else indicator_codes
    blocks = {}
    for name, function in calls.items():
        requested_codes = effective_indicator_codes.get(name)
        if requested_codes:
            results = [
                function(district_ko, normalized_ym, service_key, code, client=client)
                for code in requested_codes
            ]
            statuses_by_code = {
                code: result["source_status"]["status"]
                for code, result in zip(requested_codes, results)
            }
            if any(status == "failed" for status in statuses_by_code.values()):
                status = "failed"
            elif all(status == "no_data" for status in statuses_by_code.values()):
                status = "no_data"
            else:
                status = "ok"
            blocks[name] = {
                "source_status": {
                    "status": status,
                    "operation": OPERATION_SPECS_BY_METRIC[name],
                    "indicator_codes": statuses_by_code,
                },
                "count": sum(result.get("count", 0) for result in results),
                "items": [item for result in results for item in result.get("items", [])],
                "requests": results,
            }
        else:
            blocks[name] = function(district_ko, normalized_ym, service_key, client=client)
    statuses = {name: block["source_status"]["status"] for name, block in blocks.items()}
    if any(status == "failed" for status in statuses.values()):
        overall = "failed"
    elif all(status == "no_data" for status in statuses.values()):
        overall = "no_data"
    else:
        overall = "ok"
    return {
        "district": district_ko,
        "base_ym": normalized_ym,
        "area_cd": TOURISM_DATA_SEOUL_AREA_CODE,
        "signgu_cd": TOURISM_DATA_SEOUL_SIGNGU[district_ko],
        "source_status": {"status": overall, "operations": statuses},
        **blocks,
    }
