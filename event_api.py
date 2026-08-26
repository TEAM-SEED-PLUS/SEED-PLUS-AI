import json
from typing import Any
from common import (
        DEFAULT_SEOUL_KEY,
        build_query_context,
        build_standard_parser,
        closest_time_match,
        extract_times_from_text,
        fetch_seoul_openapi_rows,
        get_any,
        normalize_text,
        parse_loose_date,
        time_matches_band,
    )

PERFORMANCE_KEYWORDS = [
    "연극", "뮤지컬", "오페라", "무용", "클래식", "국악", "콘서트",
    "음악", "독주", "독창", "공연", "영화", "합창", "오케스트라",
]
EVENT_KEYWORDS = [
    "축제", "교육", "강연", "체험", "행사", "전시", "미술", "박람회",
    "마켓", "야시장", "포럼", "세미나", "해설", "투어",
]


def is_event_row(row: dict[str, Any]) -> bool:
    code = normalize_text(get_any(row, ["CODENAME", "CATEGORY"]))
    if any(k in code for k in PERFORMANCE_KEYWORDS) and not any(k in code for k in EVENT_KEYWORDS):
        return False
    return True


def date_matches(row: dict[str, Any], target_date) -> bool:
    start_date = parse_loose_date(get_any(row, ["STRTDATE", "START_DATE", "STARTDATE", "DATE"]))
    end_date = parse_loose_date(get_any(row, ["END_DATE", "ENDDATE", "FINISH_DATE", "DATE"]))
    if start_date and end_date:
        return start_date <= target_date <= end_date
    if start_date:
        return start_date == target_date
    raw = normalize_text(get_any(row, ["DATE"]))
    return target_date.isoformat() in raw or target_date.strftime("%Y.%m.%d") in raw


def get_events(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    seoul_key: str = DEFAULT_SEOUL_KEY,
    limit: int = 10,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    rows = []
    page_size = 1000
    for page in range(3):
        start = page * page_size + 1
        end = (page + 1) * page_size
        page_rows, _ = fetch_seoul_openapi_rows(seoul_key, "culturalEventInfo", start, end)
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break

    items = []
    for row in rows:
        if normalize_text(get_any(row, ["GUNAME", "GU_NAME", "DISTRICT"])) != ctx.district_ko:
            continue
        if not is_event_row(row):
            continue
        if not date_matches(row, ctx.target_datetime.date()):
            continue

        time_text = normalize_text(get_any(row, ["USE_TIME", "EVENT_TIME", "TIME", "DATE"]))
        candidates = extract_times_from_text(time_text)
        if not time_matches_band(candidates, ctx.time_band):
            continue
        chosen = closest_time_match(candidates, ctx.target_datetime.time())

        items.append({
            "source": "Seoul culturalEventInfo",
            "category_name": normalize_text(get_any(row, ["CODENAME", "CATEGORY"])),
            "title": normalize_text(get_any(row, ["TITLE", "EVENT_NM", "NAME"])),
            "place": normalize_text(get_any(row, ["PLACE", "LOCATION", "FACILITY"])),
            "date_text": normalize_text(get_any(row, ["DATE", "STRTDATE"])),
            "time_text": chosen.strftime("%H:%M") if chosen else time_text,
            "use_fee": normalize_text(get_any(row, ["USE_FEE", "FEE", "IS_FREE"])),
            "org_name": normalize_text(get_any(row, ["ORG_NAME", "AGENCY", "HOST"])),
            "url": normalize_text(get_any(row, ["ORG_LINK", "HMPG_ADDR", "LINK_URL"])),
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
    parser = build_standard_parser("서울 행사 API 조회")
    parser.add_argument("--seoul-key", default=DEFAULT_SEOUL_KEY, help="서울 열린데이터광장 API 키")
    args = parser.parse_args()
    result = get_events(args.district, args.date, args.time, args.seoul_key, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
