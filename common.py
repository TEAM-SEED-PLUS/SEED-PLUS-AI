import argparse
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo
import requests

SEOUL_TZ = ZoneInfo("Asia/Seoul")
SEOUL_OPENAPI_BASE = "http://openapi.seoul.go.kr:8088"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

KMA_ULTRA_SRT_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"

DEFAULT_PUBLIC_DATA_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY", "")
DEFAULT_SEOUL_KEY = os.getenv("SEOUL_OPEN_DATA_API_KEY", "")
DEFAULT_KOPIS_KEY = os.getenv("KOPIS_API_KEY", "")

DISTRICT_KO_TO_EN = {
    "강남구": "Gangnam-gu",
    "강동구": "Gangdong-gu",
    "강북구": "Gangbuk-gu",
    "강서구": "Gangseo-gu",
    "관악구": "Gwanak-gu",
    "광진구": "Gwangjin-gu",
    "구로구": "Guro-gu",
    "금천구": "Geumcheon-gu",
    "노원구": "Nowon-gu",
    "도봉구": "Dobong-gu",
    "동대문구": "Dongdaemun-gu",
    "동작구": "Dongjak-gu",
    "마포구": "Mapo-gu",
    "서대문구": "Seodaemun-gu",
    "서초구": "Seocho-gu",
    "성동구": "Seongdong-gu",
    "성북구": "Seongbuk-gu",
    "송파구": "Songpa-gu",
    "양천구": "Yangcheon-gu",
    "영등포구": "Yeongdeungpo-gu",
    "용산구": "Yongsan-gu",
    "은평구": "Eunpyeong-gu",
    "종로구": "Jongno-gu",
    "중구": "Jung-gu",
    "중랑구": "Jungnang-gu",
}
DISTRICT_EN_TO_KO = {v.lower(): k for k, v in DISTRICT_KO_TO_EN.items()}

DISTRICT_MAP_EN = DISTRICT_KO_TO_EN.copy()
DISTRICT_COORDS = {
    "강남구": {"lat": 37.5172, "lon": 127.0473},
    "강동구": {"lat": 37.5301, "lon": 127.1238},
    "강북구": {"lat": 37.6396, "lon": 127.0257},
    "강서구": {"lat": 37.5509, "lon": 126.8495},
    "관악구": {"lat": 37.4784, "lon": 126.9516},
    "광진구": {"lat": 37.5385, "lon": 127.0823},
    "구로구": {"lat": 37.4955, "lon": 126.8874},
    "금천구": {"lat": 37.4569, "lon": 126.8955},
    "노원구": {"lat": 37.6542, "lon": 127.0568},
    "도봉구": {"lat": 37.6688, "lon": 127.0471},
    "동대문구": {"lat": 37.5744, "lon": 127.0396},
    "동작구": {"lat": 37.5124, "lon": 126.9393},
    "마포구": {"lat": 37.5663, "lon": 126.9019},
    "서대문구": {"lat": 37.5792, "lon": 126.9368},
    "서초구": {"lat": 37.4837, "lon": 127.0324},
    "성동구": {"lat": 37.5635, "lon": 127.0369},
    "성북구": {"lat": 37.5894, "lon": 127.0167},
    "송파구": {"lat": 37.5145, "lon": 127.1059},
    "양천구": {"lat": 37.5170, "lon": 126.8665},
    "영등포구": {"lat": 37.5264, "lon": 126.8962},
    "용산구": {"lat": 37.5326, "lon": 126.9905},
    "은평구": {"lat": 37.6176, "lon": 126.9227},
    "종로구": {"lat": 37.5735, "lon": 126.9788},
    "중구": {"lat": 37.5637, "lon": 126.9976},
    "중랑구": {"lat": 37.6063, "lon": 127.0927},
}


KOPIS_DISTRICT_CODE_MAP = {
    "강남구": {"signgucode": "11", "signgucodesub": "1168"},
    "강동구": {"signgucode": "11", "signgucodesub": "1174"},
    "강북구": {"signgucode": "11", "signgucodesub": "1130"},
    "강서구": {"signgucode": "11", "signgucodesub": "1150"},
    "관악구": {"signgucode": "11", "signgucodesub": "1162"},
    "광진구": {"signgucode": "11", "signgucodesub": "1121"},
    "구로구": {"signgucode": "11", "signgucodesub": "1153"},
    "금천구": {"signgucode": "11", "signgucodesub": "1154"},
    "노원구": {"signgucode": "11", "signgucodesub": "1135"},
    "도봉구": {"signgucode": "11", "signgucodesub": "1132"},
    "동대문구": {"signgucode": "11", "signgucodesub": "1123"},
    "동작구": {"signgucode": "11", "signgucodesub": "1159"},
    "마포구": {"signgucode": "11", "signgucodesub": "1144"},
    "서대문구": {"signgucode": "11", "signgucodesub": "1141"},
    "서초구": {"signgucode": "11", "signgucodesub": "1165"},
    "성동구": {"signgucode": "11", "signgucodesub": "1120"},
    "성북구": {"signgucode": "11", "signgucodesub": "1129"},
    "송파구": {"signgucode": "11", "signgucodesub": "1171"},
    "양천구": {"signgucode": "11", "signgucodesub": "1147"},
    "영등포구": {"signgucode": "11", "signgucodesub": "1156"},
    "용산구": {"signgucode": "11", "signgucodesub": "1117"},
    "은평구": {"signgucode": "11", "signgucodesub": "1138"},
    "종로구": {"signgucode": "11", "signgucodesub": "1111"},
    "중구": {"signgucode": "11", "signgucodesub": "1114"},
    "중랑구": {"signgucode": "11", "signgucodesub": "1126"},
}

# TourAPI 지역코드 - 구 코드
TOURAPI_SEOUL_SIGNGU = {
    "강남구": "1",
    "강동구": "2",
    "강북구": "3",
    "강서구": "4",
    "관악구": "5",
    "광진구": "6",
    "구로구": "7",
    "금천구": "8",
    "노원구": "9",
    "도봉구": "10",
    "동대문구": "11",
    "동작구": "12",
    "마포구": "13",
    "서대문구": "14",
    "서초구": "15",
    "성동구": "16",
    "성북구": "17",
    "송파구": "18",
    "양천구": "19",
    "영등포구": "20",
    "용산구": "21",
    "은평구": "22",
    "종로구": "23",
    "중구": "24",
    "중랑구": "25",
}
TOURAPI_AREA_CODE_SEOUL = "1"

# 관광데이터랩 지역별 관광지표 API 전용 행정구역 코드.
# KorService2의 TOURAPI_SEOUL_SIGNGU(1~25)와 코드 체계가 다르다.
TOURISM_DATA_SEOUL_AREA_CODE = "11"
TOURISM_DATA_SEOUL_SIGNGU = {
    "종로구": "11110",
    "중구": "11140",
    "용산구": "11170",
    "성동구": "11200",
    "광진구": "11215",
    "동대문구": "11230",
    "중랑구": "11260",
    "성북구": "11290",
    "강북구": "11305",
    "도봉구": "11320",
    "노원구": "11350",
    "은평구": "11380",
    "서대문구": "11410",
    "마포구": "11440",
    "양천구": "11470",
    "강서구": "11500",
    "구로구": "11530",
    "금천구": "11545",
    "영등포구": "11560",
    "동작구": "11590",
    "관악구": "11620",
    "서초구": "11650",
    "강남구": "11680",
    "송파구": "11710",
    "강동구": "11740",
}

SPORTS_STADIUM_DISTRICT_HINTS = {
    "잠실": "송파구",
    "올림픽공원": "송파구",
    "상암": "마포구",
    "서울 월드컵": "마포구",
    "고척": "구로구",
    "목동": "양천구"
}

WEATHER_CODE_MAP = {
    0: "맑음", 1: "대체로 맑음", 2: "부분적으로 흐림", 3: "흐림",
    45: "안개", 48: "착빙 안개", 51: "약한 이슬비", 53: "이슬비",
    55: "강한 이슬비", 56: "약한 어는 이슬비", 57: "강한 어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "약한 어는 비",
    67: "강한 어는 비", 71: "약한 눈", 73: "눈", 75: "강한 눈",
    77: "싸락눈", 80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    85: "약한 눈소나기", 86: "강한 눈소나기", 95: "뇌우",
    96: "우박 동반 약한 뇌우", 99: "우박 동반 강한 뇌우",
}

KMA_SKY_MAP = {"1": "맑음", "3": "구름많음", "4": "흐림"}
KMA_PTY_MAP = {
    "0": "강수없음",
    "1": "비",
    "2": "비/눈",
    "3": "눈",
    "5": "빗방울",
    "6": "빗방울/눈날림",
    "7": "눈날림",
}

TIME_BANDS = {
    "심야": (0, 6 * 60),
    "아침": (6 * 60, 12 * 60),
    "점심": (12 * 60, 17 * 60),
    "오후": (17 * 60, 20 * 60),
    "저녁": (20 * 60, 24 * 60),
}

TIME_BAND_REPRESENTATIVE_TIMES = {
    "심야": "03:00",
    "아침": "09:00",
    "점심": "14:00",
    "오후": "18:00",
    "저녁": "21:00",
}


@dataclass
class QueryContext:
    district_ko: str
    district_en: str
    target_datetime: datetime
    date_str: str
    time_str: str
    time_band: str


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def safe_request_json(url: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" status={status}" if status is not None else ""
        raise RuntimeError(f"HTTP transport failure: {type(exc).__name__}{suffix}") from exc


def safe_request_text(url: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> str:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" status={status}" if status is not None else ""
        raise RuntimeError(f"HTTP transport failure: {type(exc).__name__}{suffix}") from exc


def fetch_seoul_openapi_rows(
    api_key: str,
    service_name: str,
    start: int = 1,
    end: int = 1000,
    extra_path: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"{SEOUL_OPENAPI_BASE}/{api_key}/json/{service_name}/{start}/{end}/"
    if extra_path:
        url += quote(extra_path)
    data = safe_request_json(url)
    block = data.get(service_name)
    if not isinstance(block, dict):
        block = next((v for v in data.values() if isinstance(v, dict) and "row" in v), None)
    if not isinstance(block, dict):
        raise RuntimeError(f"{service_name} 응답 형식을 해석하지 못했습니다.")
    result = block.get("RESULT", {}) if isinstance(block.get("RESULT"), dict) else {}
    code = normalize_text(result.get("CODE"))
    if code and code != "INFO-000":
        raise RuntimeError(f"{service_name} API 오류: {code} / {normalize_text(result.get('MESSAGE'))}")
    rows = block.get("row", [])
    if isinstance(rows, dict):
        rows = [rows]
    return rows, block


def normalize_district(value: str) -> tuple[str, str]:
    raw = normalize_text(value)
    if not raw:
        raise ValueError("자치구를 입력해 주세요. 예: 송파구")

    if raw in DISTRICT_KO_TO_EN:
        return raw, DISTRICT_KO_TO_EN[raw]

    if not raw.endswith("구") and f"{raw}구" in DISTRICT_KO_TO_EN:
        ko = f"{raw}구"
        return ko, DISTRICT_KO_TO_EN[ko]

    low = raw.lower()
    if low in DISTRICT_EN_TO_KO:
        ko = DISTRICT_EN_TO_KO[low]
        return ko, DISTRICT_KO_TO_EN[ko]

    raise ValueError("서울 자치구 이름을 정확히 입력해 주세요. 예: 송파구")


def parse_date_input(date_str: Optional[str]) -> datetime:
    if not date_str:
        return datetime.now(SEOUL_TZ)
    s = normalize_text(date_str)
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=SEOUL_TZ)
        except ValueError:
            continue
    raise ValueError("날짜 형식이 올바르지 않습니다. 예: 2026-04-08 또는 20260408")


def parse_time_input(time_str: Optional[str]) -> time:
    if not time_str:
        now = datetime.now(SEOUL_TZ)
        return now.time().replace(second=0, microsecond=0)
    s = normalize_text(time_str)
    for fmt in ("%H:%M", "%H%M", "%H"):
        try:
            return datetime.strptime(s, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ValueError("시간 형식이 올바르지 않습니다. 예: 09:30 또는 0930")


def classify_time_band(t: time) -> str:
    minutes = t.hour * 60 + t.minute
    for label, (start, end) in TIME_BANDS.items():
        if start <= minutes < end:
            return label
    raise ValueError(f"분류할 수 없는 시간입니다: {t}")


def resolve_time_input(time_str: Optional[str] = None,
                       time_band: Optional[str] = None) -> tuple[str, str]:
    """Normalize the public time inputs without changing pipeline semantics."""
    if time_band is not None and time_band not in TIME_BAND_REPRESENTATIVE_TIMES:
        raise ValueError("지원하지 않는 time_band입니다.")
    if time_str is None:
        resolved = (TIME_BAND_REPRESENTATIVE_TIMES[time_band] if time_band
                    else parse_time_input(None).strftime("%H:%M"))
    else:
        resolved = parse_time_input(time_str).strftime("%H:%M")
    resolved_band = classify_time_band(parse_time_input(resolved))
    if time_band is not None and resolved_band != time_band:
        raise ValueError("time과 time_band가 일치하지 않습니다.")
    return resolved, resolved_band


def build_query_context(district: str, date_str: Optional[str] = None, time_str: Optional[str] = None) -> QueryContext:
    district_ko, district_en = normalize_district(district)
    dt = parse_date_input(date_str)
    t = parse_time_input(time_str)
    target = datetime.combine(dt.date(), t, tzinfo=SEOUL_TZ)
    return QueryContext(
        district_ko=district_ko,
        district_en=district_en,
        target_datetime=target,
        date_str=target.strftime("%Y-%m-%d"),
        time_str=target.strftime("%H:%M"),
        time_band=classify_time_band(t),
    )


def time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def parse_loose_date(value: Any) -> Optional[date]:
    s = normalize_text(value)
    if not s:
        return None
    patterns = [
        "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d_%H:%M:%S", "%Y.%m.%d_%H:%M:%S", "%Y/%m/%d_%H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M", "%Y/%m/%d %H:%M",
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(20\d{2})[-./]?(\d{2})[-./]?(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def extract_times_from_text(text: str) -> list[time]:
    s = normalize_text(text)
    if not s:
        return []
    matches = re.findall(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", s)
    out = []
    for hh, mm in matches:
        try:
            out.append(time(int(hh), int(mm)))
        except ValueError:
            continue
    return out


def time_matches_band(candidates: Iterable[time], band: str) -> bool:
    values = list(candidates)
    if not values:
        return True
    return any(classify_time_band(t) == band for t in values)


def closest_time_match(candidates: Iterable[time], target: time) -> Optional[time]:
    values = list(candidates)
    if not values:
        return None
    target_m = time_to_minutes(target)
    return min(values, key=lambda t: abs(time_to_minutes(t) - target_m))


def get_any(row: dict[str, Any], keys: list[str], default: Any = "") -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def describe_weather_code(code: Any) -> str:
    try:
        return WEATHER_CODE_MAP[int(code)]
    except Exception:
        return f"코드 {code}"


def get_geo_for_district(district: str) -> tuple[float, float]:
    district_ko, district_en = normalize_district(district)

    # 서울 25개 자치구는 고정 매핑을 우선 사용해 외부 지오코딩 실패를 막는다.
    fixed = DISTRICT_COORDS.get(district_ko)
    if fixed:
        return float(fixed["lat"]), float(fixed["lon"])

    data = safe_request_json(
        OPEN_METEO_GEOCODE,
        params={"name": f"{district_en}, Seoul, South Korea", "count": 5, "language": "en", "format": "json"},
        timeout=15,
    )
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"{district_ko} 지오코딩 결과를 찾지 못했습니다.")
    target = results[0]
    for row in results:
        admin1 = normalize_text(row.get("admin1"))
        admin2 = normalize_text(row.get("admin2"))
        country = normalize_text(row.get("country"))
        if district_en.lower() in admin1.lower() or district_en.lower() in admin2.lower():
            if "korea" in country.lower():
                target = row
                break
    return float(target["latitude"]), float(target["longitude"])


def latlon_to_kma_grid(lat: float, lon: float) -> tuple[int, int]:
    RE = 6371.00877
    GRID = 5.0
    SLAT1 = 30.0
    SLAT2 = 60.0
    OLON = 126.0
    OLAT = 38.0
    XO = 43
    YO = 136

    DEGRAD = math.pi / 180.0
    re_val = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re_val * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re_val * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = math.floor(ra * math.sin(theta) + XO + 0.5)
    y = math.floor(ro - ra * math.cos(theta) + YO + 0.5)
    return int(x), int(y)


def build_standard_parser(description: str, include_district: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    if include_district:
        parser.add_argument("--district", required=True, help="서울 자치구명. 예: 송파구")
    parser.add_argument("--date", default=None, help="조회 날짜. 예: 2026-04-08 또는 20260408")
    parser.add_argument("--time", default=None, help="조회 시간. 예: 18:30 또는 1830")
    parser.add_argument("--limit", type=int, default=10, help="최대 출력 개수")
    return parser
