import json
from typing import Any, Optional
from urllib.parse import unquote
import requests
from common import (
        DEFAULT_PUBLIC_DATA_KEY,
        build_query_context,
        build_standard_parser,
        closest_time_match,
        extract_times_from_text,
        normalize_text,
        parse_loose_date,
        time_matches_band,
    )

# searchFestival2에서 지역 필터로 잘 동작하는 lDong 코드 사용
# (노트북에서 사용한 형식과 동일한 계열)
SEOUL_LDONG_REGN_CD = "11"
SEOUL_LDONG_SIGNGU = {
    "종로구": "110",
    "중구": "140",
    "용산구": "170",
    "성동구": "200",
    "광진구": "215",
    "동대문구": "230",
    "중랑구": "260",
    "성북구": "290",
    "강북구": "305",
    "도봉구": "320",
    "노원구": "350",
    "은평구": "380",
    "서대문구": "410",
    "마포구": "440",
    "양천구": "470",
    "강서구": "500",
    "구로구": "530",
    "금천구": "545",
    "영등포구": "560",
    "동작구": "590",
    "관악구": "620",
    "서초구": "650",
    "강남구": "680",
    "송파구": "710",
    "강동구": "740",
}


class FestivalAPI:
    BASE_URL = "https://apis.data.go.kr/B551011/KorService2"

    def __init__(
        self,
        service_key: str,
        mobile_os: str = "ETC",
        mobile_app: str = "CityInfoApp",
        timeout: int = 15,
    ):
        self.service_key = unquote(service_key)
        self.mobile_os = mobile_os
        self.mobile_app = mobile_app
        self.timeout = timeout

    def _call(self, operation: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{operation}"
        all_params = {
            "serviceKey": self.service_key,
            "MobileOS": self.mobile_os,
            "MobileApp": self.mobile_app,
            "_type": "json",
        }
        if params:
            all_params.update(params)

        try:
            res = requests.get(url, params=all_params, timeout=self.timeout)
            res.raise_for_status()
            data = res.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"TourAPI transport failure: {type(exc).__name__}") from exc

        response = data.get("response", {})
        if not isinstance(response, dict):
            response = {}
        header = response.get("header", {})
        if not isinstance(header, dict):
            header = {}

        code = header.get("resultCode")
        if code not in ("0000", "00", None):
            raise RuntimeError(f"TourAPI 오류: {code} / {header.get('resultMsg')}")
        return data

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _extract_items(cls, data: dict[str, Any]) -> dict[str, Any]:
        response = data.get("response", {})
        if not isinstance(response, dict):
            response = {}

        body = response.get("body", {})
        if not isinstance(body, dict):
            body = {}

        raw_items = body.get("items", {})
        if isinstance(raw_items, dict):
            items = raw_items.get("item", [])
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            items = []

        if isinstance(items, dict):
            items = [items]
        elif not isinstance(items, list):
            items = []

        items = [x for x in items if isinstance(x, dict)]
        return {
            "items": items,
            "pageNo": cls._safe_int(body.get("pageNo"), 1),
            "numOfRows": cls._safe_int(body.get("numOfRows"), 0),
            "totalCount": cls._safe_int(body.get("totalCount"), 0),
        }

    def search_festival(
        self,
        target_date: str,
        district_ko: str,
        num_of_rows: int = 200,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """
        자치구 기준으로 축제 검색.
        - 날짜는 target_date에 해당하는 진행 중 축제만 남김
        - 자치구는 lDong 코드 + 주소/장소 문자열로 한 번 더 검증
        - 시간 정보는 여기서 자르지 않음
        """
        signgu = SEOUL_LDONG_SIGNGU.get(district_ko)
        target_compact = target_date.replace("-", "")

        results: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        for page in range(1, max_pages + 1):
            params = {
                "pageNo": page,
                "numOfRows": num_of_rows,
                "arrange": "C",
                "eventStartDate": target_compact,
                "eventEndDate": target_compact,
                "lDongRegnCd": SEOUL_LDONG_REGN_CD,
            }
            if signgu:
                params["lDongSignguCd"] = signgu

            payload = self._call("searchFestival2", params)
            extracted = self._extract_items(payload)
            items = extracted["items"]

            if not items:
                break

            for item in items:
                title = normalize_text(item.get("title"))
                addr1 = normalize_text(item.get("addr1"))
                addr2 = normalize_text(item.get("addr2"))
                eventplace = normalize_text(item.get("eventplace"))
                tel = normalize_text(item.get("tel"))
                event_time_text = normalize_text(item.get("playtime") or item.get("usetimefestival"))
                start_raw = normalize_text(item.get("eventstartdate"))
                end_raw = normalize_text(item.get("eventenddate"))

                # 날짜 검증: 시작/종료일이 있으면 target_date가 범위 안에 있어야 함
                start_d = parse_loose_date(start_raw)
                end_d = parse_loose_date(end_raw)
                target_d = parse_loose_date(target_compact)
                if target_d and start_d and end_d:
                    if not (start_d <= target_d <= end_d):
                        continue
                elif target_d and start_d and target_d < start_d:
                    continue
                elif target_d and end_d and target_d > end_d:
                    continue

                # 자치구 문자열 검증: district 기준으로 모두 주되, 주소/장소가 구를 가리키는 경우 우선
                text_blob = " ".join([addr1, addr2, eventplace, title, tel])
                if district_ko not in text_blob:
                    # lDongSignguCd로 이미 1차 필터링했더라도, 문자열 상 흔적이 전혀 없는 경우는 제외
                    continue

                key = (
                    item.get("contentid"),
                    title,
                    addr1,
                    start_raw,
                    end_raw,
                )
                if key in seen:
                    continue
                seen.add(key)

                new_item = dict(item)
                new_item["event_time_text"] = event_time_text
                results.append(new_item)

            # 마지막 페이지 추정
            if len(items) < num_of_rows:
                break

        return results


def get_festivals(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    public_key: str = DEFAULT_PUBLIC_DATA_KEY,
    limit: int = 10,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    api = FestivalAPI(public_key)
    items = api.search_festival(ctx.date_str, ctx.district_ko, num_of_rows=200, max_pages=20)

    filtered = []
    for item in items:
        time_text = normalize_text(item.get("event_time_text"))
        candidates = extract_times_from_text(time_text)

        # 사용자가 원하는 규칙:
        # - 시간이 있으면 시간대 반영
        # - 시간이 없으면 날짜만 맞으면 그냥 포함
        if candidates and not time_matches_band(candidates, ctx.time_band):
            continue

        chosen = closest_time_match(candidates, ctx.target_datetime.time())
        filtered.append({
            "source": "TourAPI searchFestival2",
            "title": normalize_text(item.get("title")),
            "place": normalize_text(item.get("eventplace")),
            "address": " ".join(
                x for x in [normalize_text(item.get("addr1")), normalize_text(item.get("addr2"))] if x
            ).strip(),
            "event_start_date": normalize_text(item.get("eventstartdate")),
            "event_end_date": normalize_text(item.get("eventenddate")),
            "time_text": chosen.strftime("%H:%M") if chosen else time_text,
            "original_time_text": time_text,
            "tel": normalize_text(item.get("tel")),
            "homepage": normalize_text(item.get("homepage")),
            "contentid": normalize_text(item.get("contentid")),
            "firstimage": normalize_text(item.get("firstimage")),
            "mapx": normalize_text(item.get("mapx")),
            "mapy": normalize_text(item.get("mapy")),
        })

    filtered.sort(
        key=lambda x: (
            x["time_text"] == "",
            x["time_text"],
            x["title"],
        )
    )

    return {
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "count": len(filtered),
        "items": filtered[:limit],
    }


def main() -> None:
    parser = build_standard_parser("축제 API 조회")
    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_DATA_KEY, help="공공데이터포털 서비스키")
    args = parser.parse_args()
    result = get_festivals(args.district, args.date, args.time, args.public_key, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
