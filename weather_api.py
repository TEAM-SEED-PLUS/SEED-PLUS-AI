import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import unquote
from common import (
        DEFAULT_PUBLIC_DATA_KEY,
        KMA_PTY_MAP,
        KMA_SKY_MAP,
        KMA_ULTRA_SRT_URL,
        OPEN_METEO_ARCHIVE,
        OPEN_METEO_FORECAST,
        SEOUL_TZ,
        build_query_context,
        build_standard_parser,
        describe_weather_code,
        get_geo_for_district,
        latlon_to_kma_grid,
        safe_request_json,
        safe_request_text,
    )


def get_ultra_srt_base_datetime(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(SEOUL_TZ)
    if now.minute < 45:
        return now.replace(minute=30, second=0, microsecond=0) - timedelta(hours=1)
    return now.replace(minute=30, second=0, microsecond=0)


def weather_text_from_codes(sky: str | None, pty: str | None) -> str:
    if pty and pty != "0":
        return KMA_PTY_MAP.get(pty, "알 수 없음")
    return KMA_SKY_MAP.get(sky or "", "알 수 없음")


def fetch_open_meteo_weather(district: str, date_str: str | None = None, time_str: str | None = None) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    lat, lon = get_geo_for_district(ctx.district_ko)

    target_date = ctx.target_datetime.date()
    today = datetime.now(SEOUL_TZ).date()
    url = OPEN_METEO_ARCHIVE if target_date < today else OPEN_METEO_FORECAST

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,weathercode,windspeed_10m",
        "timezone": "Asia/Seoul",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    data = safe_request_json(url, params=params, timeout=20)
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError("Open-Meteo 시간별 예보가 없습니다.")

    target_hour = ctx.target_datetime.strftime("%Y-%m-%dT%H:00")
    idx = min(range(len(times)), key=lambda i: abs(hash(times[i]) - hash(target_hour)))
    # 시간 문자열끼리 가장 가까운 시간으로 찾기
    parsed = []
    for t in times:
        try:
            parsed.append(datetime.fromisoformat(t))
        except ValueError:
            parsed.append(None)
    if any(x is not None for x in parsed):
        target = ctx.target_datetime.replace(tzinfo=None)
        idx = min(
            [i for i, dt in enumerate(parsed) if dt is not None],
            key=lambda i: abs(parsed[i] - target),
        )

    return {
        "source": "Open-Meteo",
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "latitude": lat,
        "longitude": lon,
        "forecast_time": times[idx],
        "summary": describe_weather_code((hourly.get("weathercode") or [None])[idx]),
        "temperature_c": (hourly.get("temperature_2m") or [None])[idx],
        "humidity_percent": (hourly.get("relative_humidity_2m") or [None])[idx],
        "precipitation_probability_percent": (hourly.get("precipitation_probability") or [None])[idx],
        "precipitation_mm": (hourly.get("precipitation") or [None])[idx],
        "wind_speed_kmh": (hourly.get("windspeed_10m") or [None])[idx],
    }


def fetch_kma_ultra_short_weather(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    service_key: str = DEFAULT_PUBLIC_DATA_KEY,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    if ctx.target_datetime.date() != datetime.now(SEOUL_TZ).date():
        raise RuntimeError("KMA 초단기예보는 오늘 날짜 조회에 적합합니다.")

    lat, lon = get_geo_for_district(ctx.district_ko)
    nx, ny = latlon_to_kma_grid(lat, lon)
    base_dt = get_ultra_srt_base_datetime()

    params = {
        "serviceKey": unquote(service_key),
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "XML",
        "base_date": base_dt.strftime("%Y%m%d"),
        "base_time": base_dt.strftime("%H%M"),
        "nx": str(nx),
        "ny": str(ny),
    }
    xml_text = safe_request_text(KMA_ULTRA_SRT_URL, params=params, timeout=15).strip()
    if not xml_text.startswith("<"):
        raise RuntimeError(f"KMA XML 응답이 아닙니다: {xml_text[:200]}")
    root = ET.fromstring(xml_text)

    result_code = (root.findtext(".//header/resultCode") or "").strip()
    result_msg = (root.findtext(".//header/resultMsg") or "").strip()
    if result_code not in {"0", "00"}:
        raise RuntimeError(f"KMA API 오류: {result_code} / {result_msg}")

    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for item in root.findall(".//item"):
        fcst_date = (item.findtext("fcstDate") or "").strip()
        fcst_time = (item.findtext("fcstTime") or "").strip()
        category = (item.findtext("category") or "").strip()
        fcst_value = (item.findtext("fcstValue") or "").strip()
        key = (fcst_date, fcst_time)
        grouped.setdefault(key, {})
        grouped[key][category] = fcst_value

    if not grouped:
        raise RuntimeError("KMA 예보 항목이 없습니다.")

    target = ctx.target_datetime
    def slot_dt(key: tuple[str, str]) -> datetime:
        return datetime.strptime(key[0] + key[1], "%Y%m%d%H%M").replace(tzinfo=SEOUL_TZ)

    chosen_key = min(grouped.keys(), key=lambda x: abs(slot_dt(x) - target))
    slot = grouped[chosen_key]
    wind_kmh = None
    try:
        wind_kmh = round(float(slot.get("WSD", "")) * 3.6, 2)
    except Exception:
        wind_kmh = None

    return {
        "source": "KMA UltraSrtFcst",
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "grid": {"nx": nx, "ny": ny},
        "forecast_time": f"{chosen_key[0]} {chosen_key[1]}",
        "summary": weather_text_from_codes(slot.get("SKY"), slot.get("PTY")),
        "temperature_c": slot.get("T1H"),
        "humidity_percent": slot.get("REH"),
        "precipitation_probability_percent": None,
        "precipitation_mm": slot.get("RN1"),
        "wind_speed_kmh": wind_kmh,
    }


def get_weather(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    public_key: str = DEFAULT_PUBLIC_DATA_KEY,
) -> dict[str, Any]:
    try:
        return fetch_kma_ultra_short_weather(district, date_str, time_str, public_key)
    except Exception as exc:
        weather = fetch_open_meteo_weather(district, date_str, time_str)
        weather["fallback_reason"] = str(exc)
        return weather


def main() -> None:
    parser = build_standard_parser("날씨 API 조회")
    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_DATA_KEY, help="공공데이터포털 서비스키")
    args = parser.parse_args()
    result = get_weather(args.district, args.date, args.time, args.public_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
