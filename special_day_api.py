import json
from datetime import datetime
from typing import Any, Optional
from urllib.parse import unquote
import requests
from common import DEFAULT_PUBLIC_DATA_KEY, build_standard_parser


class SpcdeInfoClient:
    BASE_URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService"

    def __init__(self, service_key: str, timeout: int = 15):
        self.service_key = unquote(service_key)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
        })

    def request(self, operation: str, year: str, month: str, num_of_rows: int = 100) -> list[dict[str, Any]]:
        url = f"{self.BASE_URL}/{operation}"
        params = {
            "solYear": year,
            "solMonth": month,
            "ServiceKey": self.service_key,
            "_type": "json",
            "numOfRows": num_of_rows,
        }
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"특일 API transport failure: {type(exc).__name__}") from exc

        response_obj = data.get("response", {})
        header = response_obj.get("header", {}) if isinstance(response_obj, dict) else {}
        code = header.get("resultCode")
        if code not in (None, "00"):
            raise RuntimeError(f"API 오류: resultCode={code}, resultMsg={header.get('resultMsg')}")

        body = response_obj.get("body", {}) if isinstance(response_obj, dict) else {}
        items_obj = body.get("items", {})
        if not isinstance(items_obj, dict):
            return []
        raw_items = items_obj.get("item", [])
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            return []
        return [x for x in raw_items if isinstance(x, dict)]


def normalize_target_date(target_date: Optional[str] = None) -> str:
    if target_date is None or not str(target_date).strip():
        return datetime.now().strftime("%Y%m%d")
    s = str(target_date).strip().replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError("날짜 형식은 YYYYMMDD 또는 YYYY-MM-DD 이어야 합니다.")
    datetime.strptime(s, "%Y%m%d")
    return s


def get_special_day_info(service_key: str = DEFAULT_PUBLIC_DATA_KEY, target_date: Optional[str] = None) -> dict[str, Any]:
    client = SpcdeInfoClient(service_key)
    yyyymmdd = normalize_target_date(target_date)
    dt = datetime.strptime(yyyymmdd, "%Y%m%d")
    year = dt.strftime("%Y")
    month = dt.strftime("%m")

    operations = {
        "국경일": "getHoliDeInfo",
        "공휴일": "getRestDeInfo",
        "기념일": "getAnniversaryInfo",
        "24절기": "get24DivisionsInfo",
        "잡절": "getSundryDayInfo",
    }

    result = {"date": yyyymmdd, "items": []}
    errors = []
    for category, operation in operations.items():
        try:
            items = client.request(operation, year, month)
            for item in items:
                if str(item.get("locdate", "")) == yyyymmdd:
                    result["items"].append({
                        "category": category,
                        "dateName": item.get("dateName", ""),
                        "isHoliday": item.get("isHoliday", item.get("isholiday", "")),
                        "dateKind": item.get("dateKind", ""),
                        "locdate": item.get("locdate", ""),
                        "seq": item.get("seq", ""),
                        "kst": item.get("kst", ""),
                        "sunLongitude": item.get("sunLongitude", ""),
                    })
        except Exception as exc:
            errors.append({"category": category, "error": str(exc)})
    if errors:
        result["errors"] = errors
    if errors:
        result["source_status"] = {
            "status": "failed",
            "source": "special_day",
            "reason": f"특일 API {len(errors)}/{len(operations)}개 호출 실패",
        }
    return result


def main() -> None:
    parser = build_standard_parser("공휴일/특일 API 조회", include_district=False)
    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_DATA_KEY, help="공공데이터포털 서비스키")
    args = parser.parse_args()
    result = get_special_day_info(args.public_key, args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
