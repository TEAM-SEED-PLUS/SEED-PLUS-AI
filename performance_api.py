import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
import requests
from common import (
        DEFAULT_KOPIS_KEY,
        KOPIS_DISTRICT_CODE_MAP,
        build_query_context,
        build_standard_parser,
        closest_time_match,
        extract_times_from_text,
        normalize_text,
        time_matches_band,
    )


class KOPISClient:
    BASE_URL = "http://www.kopis.or.kr/openApi/restful"

    def __init__(self, service_key: str, timeout: int = 15):
        self.service_key = service_key
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any]) -> ET.Element:
        url = f"{self.BASE_URL}/{path}"
        try:
            response = requests.get(url, params={k: v for k, v in params.items() if v is not None}, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"KOPIS transport failure: {type(exc).__name__}") from exc
        try:
            return ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise ValueError(f"KOPIS XML 파싱 실패: {exc}\n응답 일부: {response.text[:500]}")

    @staticmethod
    def _safe_text(node: Optional[ET.Element], tag: str, default: str = "") -> str:
        child = node.find(tag) if node is not None else None
        return child.text.strip() if child is not None and child.text else default

    def get_performances_page(
        self,
        stdate: str,
        eddate: str,
        cpage: int = 1,
        rows: int = 100,
        signgucode: Optional[str] = None,
        signgucodesub: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        root = self._get(
            "pblprfr",
            {
                "service": self.service_key,
                "stdate": stdate,
                "eddate": eddate,
                "cpage": cpage,
                "rows": rows,
                "signgucode": signgucode,
                "signgucodesub": signgucodesub,
            },
        )
        results = []
        for db in root.findall(".//db"):
            error_code = self._safe_text(db, "returncode")
            if error_code and error_code not in {"00", "INFO-000"}:
                # KOPIS reports authentication and request errors as HTTP 200
                # XML <db> records. Do not misclassify them as valid/empty data.
                raise RuntimeError(f"KOPIS API failure: code={error_code}")
            results.append(
                {
                    "mt20id": self._safe_text(db, "mt20id"),
                    "prfnm": self._safe_text(db, "prfnm"),
                    "prfpdfrom": self._safe_text(db, "prfpdfrom"),
                    "prfpdto": self._safe_text(db, "prfpdto"),
                    "fcltynm": self._safe_text(db, "fcltynm"),
                    "poster": self._safe_text(db, "poster"),
                    "area": self._safe_text(db, "area"),
                    "genrenm": self._safe_text(db, "genrenm"),
                    "prfstate": self._safe_text(db, "prfstate"),
                    "openrun": self._safe_text(db, "openrun"),
                }
            )
        return results

    def get_all_performances(
        self,
        stdate: str,
        eddate: str,
        signgucode: Optional[str] = None,
        signgucodesub: Optional[str] = None,
        rows: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        all_items = []
        seen_ids = set()
        for page in range(1, max_pages + 1):
            items = self.get_performances_page(
                stdate=stdate,
                eddate=eddate,
                cpage=page,
                rows=rows,
                signgucode=signgucode,
                signgucodesub=signgucodesub,
            )
            if not items:
                break
            new_count = 0
            for item in items:
                mt20id = item.get("mt20id")
                if mt20id and mt20id not in seen_ids:
                    seen_ids.add(mt20id)
                    all_items.append(item)
                    new_count += 1
            if len(items) < rows or new_count == 0:
                break
        return all_items

    def get_performance_detail(self, mt20id: str) -> dict[str, Any]:
        root = self._get(f"pblprfr/{mt20id}", {"service": self.service_key})
        db = root.find(".//db")
        if db is None:
            return {}
        return {
            "mt20id": self._safe_text(db, "mt20id"),
            "mt10id": self._safe_text(db, "mt10id"),
            "prfnm": self._safe_text(db, "prfnm"),
            "prfruntime": self._safe_text(db, "prfruntime"),
            "pcseguidance": self._safe_text(db, "pcseguidance"),
            "dtguidance": self._safe_text(db, "dtguidance"),
            "sty": self._safe_text(db, "sty"),
            "genrenm": self._safe_text(db, "genrenm"),
        }

    def get_facility_detail(self, mt10id: str) -> dict[str, Any]:
        root = self._get(f"prfplc/{mt10id}", {"service": self.service_key})
        db = root.find(".//db")
        if db is None:
            return {}
        return {
            "mt10id": self._safe_text(db, "mt10id"),
            "fcltynm": self._safe_text(db, "fcltynm"),
            "adres": self._safe_text(db, "adres"),
            "la": self._safe_text(db, "la"),
            "lo": self._safe_text(db, "lo"),
            "seatscale": self._safe_text(db, "seatscale"),
        }


def extract_gu_from_address(address: str) -> str:
    if not address:
        return ""
    match = re.search(r"(서울\s*)?([가-힣]+구)", address)
    return match.group(2) if match else ""


def get_region_codes_by_district(district_ko: Optional[str]) -> dict[str, Optional[str]]:
    if not district_ko:
        return {"signgucode": None, "signgucodesub": None}
    if district_ko not in KOPIS_DISTRICT_CODE_MAP:
        raise ValueError(f"지원하지 않는 구입니다: {district_ko}")
    return KOPIS_DISTRICT_CODE_MAP[district_ko]


def get_performances(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    kopis_key: str = DEFAULT_KOPIS_KEY,
    limit: int = 10,
    include_facility_detail: bool = True,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    client = KOPISClient(kopis_key)
    region_info = get_region_codes_by_district(ctx.district_ko)

    rows = client.get_all_performances(
        stdate=ctx.date_str.replace("-", ""),
        eddate=ctx.date_str.replace("-", ""),
        signgucode=region_info["signgucode"],
        signgucodesub=region_info["signgucodesub"],
    )

    facility_cache: dict[str, dict[str, Any]] = {}
    detail_cache: dict[str, dict[str, Any]] = {}
    items = []

    for row in rows:
        mt20id = row.get("mt20id", "")
        detail = {}
        facility = {}
        if include_facility_detail and mt20id:
            if mt20id not in detail_cache:
                try:
                    detail_cache[mt20id] = client.get_performance_detail(mt20id)
                except Exception as exc:
                    detail_cache[mt20id] = {"error": str(exc)}
            detail = detail_cache[mt20id]
            mt10id = detail.get("mt10id")
            if mt10id:
                if mt10id not in facility_cache:
                    try:
                        facility_cache[mt10id] = client.get_facility_detail(mt10id)
                    except Exception as exc:
                        facility_cache[mt10id] = {"error": str(exc)}
                facility = facility_cache[mt10id]

        dtguidance = normalize_text(detail.get("dtguidance"))
        candidates = extract_times_from_text(dtguidance)
        if not time_matches_band(candidates, ctx.time_band):
            continue
        chosen = closest_time_match(candidates, ctx.target_datetime.time())

        address = normalize_text(facility.get("adres"))
        district_from_addr = extract_gu_from_address(address)
        if district_from_addr and district_from_addr != ctx.district_ko:
            continue

        items.append({
            "source": "KOPIS",
            "title": normalize_text(row.get("prfnm")),
            "genre": normalize_text(row.get("genrenm") or detail.get("genrenm")),
            "place": normalize_text(row.get("fcltynm") or facility.get("fcltynm")),
            "address": address,
            "district_from_address": district_from_addr,
            "date_from": normalize_text(row.get("prfpdfrom")),
            "date_to": normalize_text(row.get("prfpdto")),
            "time_text": chosen.strftime("%H:%M") if chosen else dtguidance,
            "runtime": normalize_text(detail.get("prfruntime")),
            "price_text": normalize_text(detail.get("pcseguidance")),
            "openrun": normalize_text(row.get("openrun")),
            "poster": normalize_text(row.get("poster")),
        })

    items.sort(key=lambda x: (x["time_text"] == "", x["time_text"], x["title"]))
    return {
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "count": len(items),
        "items": items[:limit],
    }


def main() -> None:
    parser = build_standard_parser("공연 API 조회")
    parser.add_argument("--kopis-key", default=DEFAULT_KOPIS_KEY, help="KOPIS 서비스키")
    parser.add_argument("--no-detail", action="store_true", help="시설 상세 조회 비활성화")
    args = parser.parse_args()
    result = get_performances(
        args.district,
        args.date,
        args.time,
        args.kopis_key,
        args.limit,
        include_facility_detail=not args.no_detail,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
