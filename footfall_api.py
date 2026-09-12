import json
from typing import Any
from urllib.parse import quote
import pandas as pd
import requests
from common import (
        DEFAULT_SEOUL_KEY,
        DISTRICT_MAP_EN,
        build_query_context,
        build_standard_parser,
        classify_time_band,
    )

BASE_URL = "http://openapi.seoul.go.kr:8088"
SERVICE_NAME = "IotVdata018"


def normalize_date(date_str: str) -> str:
    date_str = str(date_str).strip()
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
        return date_str
    raise ValueError("날짜 형식은 YYYYMMDD 또는 YYYY-MM-DD 여야 합니다.")


def _fetch_page(api_key: str, district_value: str, start: int, end: int) -> dict[str, Any]:
    url = f"{BASE_URL}/{api_key}/json/{SERVICE_NAME}/{start}/{end}/{quote(district_value)}"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"S-DoT transport failure: {type(exc).__name__}") from exc

    # 1) 정상 응답: { "IotVdata018": { ... } }
    if SERVICE_NAME in data:
        root = data[SERVICE_NAME]
        result = root.get("RESULT", {})
        code = result.get("CODE")
        if code not in ("INFO-000", None):
            raise ValueError(f"API 오류: {code} / {result.get('MESSAGE', '')}")
        return root

    # 2) 실패 응답: { "RESULT": { ... } }
    if "RESULT" in data:
        result = data["RESULT"]
        code = result.get("CODE")

        if code == "INFO-200":
            return {
                "list_total_count" : 0,
                "row" : [],
                "_no_data" : True,
                "RESULT" : result
            }
        raise ValueError(f"API 오류: {code} / {result.get('MESSAGE', '')}")

    # 3) 예상 밖 응답
    raise ValueError(f"예상하지 못한 응답 형식: {data}")


def get_sdot_visitor_data(api_key: str, district: str, target_date: str, page_size: int = 1000,
                          allow_english_fallback: bool = True) -> pd.DataFrame:
    target_date = normalize_date(target_date)

    district_candidates = [district]
    if allow_english_fallback and district in DISTRICT_MAP_EN:
        district_candidates.append(DISTRICT_MAP_EN[district])

    rows = []
    last_error: Exception | None = None

    for district_value in district_candidates:
        try:
            first_root = _fetch_page(api_key, district_value, 1, 1)
            if first_root.get("_no_data"):
                return pd.DataFrame()
            total_count = int(first_root.get("list_total_count", 0))
            if total_count == 0:
                continue
            rows = []
            for start in range(1, total_count + 1, page_size):
                end = min(start + page_size - 1, total_count)
                root = _fetch_page(api_key, district_value, start, end)
                rows.extend(root.get("row", []))
            if rows:
                break
        except Exception as exc:
            last_error = exc

    if not rows:
        if last_error:
            raise last_error
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["SENSING_DT"] = pd.to_datetime(df["SENSING_TIME"].astype(str).str.replace("_", " ", regex=False), errors="coerce")
    df["REG_DT"] = pd.to_datetime(df["REG_DTTM"], errors="coerce")
    df["SENSING_DATE"] = df["SENSING_DT"].dt.strftime("%Y-%m-%d")
    df["VISITOR_COUNT"] = pd.to_numeric(df["VISITOR_COUNT"], errors="coerce")
    filtered = df[df["SENSING_DATE"] == target_date].copy()
    if filtered.empty:
        return filtered
    return filtered.sort_values(by=["SENSING_DT", "ADMINISTRATIVE_DISTRICT"]).reset_index(drop=True)


def get_footfall(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    seoul_key: str = DEFAULT_SEOUL_KEY,
    limit: int = 10,
    allow_english_fallback: bool = True,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    df = get_sdot_visitor_data(seoul_key, ctx.district_ko, ctx.date_str,
                               allow_english_fallback=allow_english_fallback)

    if df.empty:
        return {
            "district": ctx.district_ko,
            "date": ctx.date_str,
            "time": ctx.time_str,
            "time_band": ctx.time_band,
            "count": 0,
            "items": [],
            "summary": {},
        }

    df["TIME_BAND"] = df["SENSING_DT"].dt.time.apply(classify_time_band)
    band_df = df[df["TIME_BAND"] == ctx.time_band].copy()
    used_all_day_fallback = band_df.empty
    if band_df.empty:
        band_df = df.copy()

    target_minutes = ctx.target_datetime.hour * 60 + ctx.target_datetime.minute
    band_df["MINUTE_GAP"] = band_df["SENSING_DT"].dt.hour * 60 + band_df["SENSING_DT"].dt.minute - target_minutes
    band_df["ABS_MINUTE_GAP"] = band_df["MINUTE_GAP"].abs()
    band_df = band_df.sort_values(by=["ABS_MINUTE_GAP", "SENSING_DT", "ADMINISTRATIVE_DISTRICT"])

    items = []
    for _, row in band_df.head(limit).iterrows():
        items.append({
            "sensing_time": row["SENSING_DT"].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(row["SENSING_DT"]) else "",
            "administrative_district": row.get("ADMINISTRATIVE_DISTRICT"),
            "visitor_count": int(row["VISITOR_COUNT"]) if pd.notna(row["VISITOR_COUNT"]) else None,
            "region": row.get("REGION"),
            "serial_no": row.get("SERIAL_NO"),
        })

    summary = {
        "records_total": int(len(df)),
        "records_in_band": int(len(df[df["TIME_BAND"] == ctx.time_band])),
        "visitor_sum_in_band": int(band_df["VISITOR_COUNT"].fillna(0).sum()),
        "visitor_avg_in_band": round(float(band_df["VISITOR_COUNT"].dropna().mean()), 2) if band_df["VISITOR_COUNT"].dropna().size else None,
        "visitor_max_in_band": int(band_df["VISITOR_COUNT"].dropna().max()) if band_df["VISITOR_COUNT"].dropna().size else None,
        "visitor_min_in_band": int(band_df["VISITOR_COUNT"].dropna().min()) if band_df["VISITOR_COUNT"].dropna().size else None,
    }

    result = {
        "district": ctx.district_ko,
        "date": ctx.date_str,
        "time": ctx.time_str,
        "time_band": ctx.time_band,
        "count": len(items),
        "items": items,
        "summary": summary,
    }
    if used_all_day_fallback:
        result["fallback_reason"] = f"{ctx.time_band} 데이터가 없어 당일 전체 데이터를 사용했습니다."
    return result


def main() -> None:
    parser = build_standard_parser("유동인구 API 조회")
    parser.add_argument("--seoul-key", default=DEFAULT_SEOUL_KEY, help="서울 열린데이터광장 API 키")
    args = parser.parse_args()
    result = get_footfall(args.district, args.date, args.time, args.seoul_key, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
