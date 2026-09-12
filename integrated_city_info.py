import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from common import DEFAULT_KOPIS_KEY, DEFAULT_PUBLIC_DATA_KEY, DEFAULT_SEOUL_KEY, build_query_context
from event_api import get_events
from festival_api import get_festivals
from footfall_api import get_footfall
from oa21285_footfall import load_available_oa_footfall, resolve_indicator_footfall_sources
from performance_cache import get_cached_performances
from special_day_api import get_special_day_info
from sports_cache import get_cached_sports
from weather_api import get_weather
from source_status import collect_with_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="서울 자치구 기준 통합 정보 조회")
    parser.add_argument("--district", required=True, help="서울 자치구명. 예: 송파구")
    parser.add_argument("--date", default=None, help="조회 날짜. 예: 2026-04-08 또는 20260408")
    parser.add_argument("--time", default=None, help="조회 시간. 예: 18:30 또는 1830")
    parser.add_argument("--public-key", default=DEFAULT_PUBLIC_DATA_KEY, help="공공데이터포털 서비스키")
    parser.add_argument("--seoul-key", default=DEFAULT_SEOUL_KEY, help="서울 열린데이터광장 API 키")
    parser.add_argument("--kopis-key", default=DEFAULT_KOPIS_KEY, help="KOPIS 서비스키")
    parser.add_argument("--culture-limit", type=int, default=10, help="공연/행사/축제 최대 출력 개수")
    parser.add_argument("--sports-limit", type=int, default=10, help="스포츠 최대 출력 개수")
    parser.add_argument("--footfall-limit", type=int, default=10, help="유동인구 샘플 최대 출력 개수")
    parser.add_argument("--output-json", default=None, help="저장할 JSON 경로")
    return parser


def print_section(title: str, block: dict[str, Any]) -> None:
    print("=" * 100)
    print(title)
    print("=" * 100)
    count = block.get("count")
    if count is not None:
        print(f"건수: {count}")
    items = block.get("items", [])
    if not items:
        print("데이터 없음")
        return
    for idx, item in enumerate(items, start=1):
        print(f"[{idx}] {json.dumps(item, ensure_ascii=False)}")


def get_all_city_info(
    district: str,
    date_str: str | None = None,
    time_str: str | None = None,
    public_key: str = DEFAULT_PUBLIC_DATA_KEY,
    seoul_key: str = DEFAULT_SEOUL_KEY,
    kopis_key: str = DEFAULT_KOPIS_KEY,
    culture_limit: int = 10,
    sports_limit: int = 10,
    footfall_limit: int = 10,
) -> dict[str, Any]:
    ctx = build_query_context(district, date_str, time_str)
    result: dict[str, Any] = {
        "query": {
            "district": ctx.district_ko,
            "date": ctx.date_str,
            "time": ctx.time_str,
            "time_band": ctx.time_band,
        }
    }

    live_jobs = {
        "weather": (get_weather, (ctx.district_ko, ctx.date_str, ctx.time_str, public_key)),
        "festival": (get_festivals, (ctx.district_ko, ctx.date_str, ctx.time_str, public_key, culture_limit)),
        "event": (get_events, (ctx.district_ko, ctx.date_str, ctx.time_str, seoul_key, culture_limit)),
        "special_day": (get_special_day_info, (public_key, ctx.date_str)),
    }
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(collect_with_status, name, fetcher, *args): name
                   for name, (fetcher, args) in live_jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            # collect_with_status contains source-local failures; keep this guard
            # so an unexpected Future failure cannot cancel sibling sources.
            try:
                result[name] = future.result()
            except Exception as exc:
                result[name] = collect_with_status(name, lambda: (_ for _ in ()).throw(exc))
    result["performance"] = get_cached_performances(
        ctx.district_ko, ctx.date_str, ctx.time_str, kopis_key, culture_limit)
    result["sports"] = get_cached_sports(ctx.district_ko, ctx.date_str, ctx.time_str, sports_limit)
    oa_footfall = load_available_oa_footfall(ctx.district_ko, ctx.date_str, ctx.time_band)
    result["footfall"] = resolve_indicator_footfall_sources(oa_footfall, lambda: collect_with_status(
        "footfall", get_footfall, ctx.district_ko, ctx.date_str, ctx.time_str, seoul_key, footfall_limit, False
    ))
    result["source_status"] = {
        key: result[key]["source_status"]
        for key in ("weather", "festival", "event", "performance", "sports", "footfall", "special_day")
    }
    return result


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    result = get_all_city_info(
        district=args.district,
        date_str=args.date,
        time_str=args.time,
        public_key=args.public_key,
        seoul_key=args.seoul_key,
        kopis_key=args.kopis_key,
        culture_limit=args.culture_limit,
        sports_limit=args.sports_limit,
        footfall_limit=args.footfall_limit,
    )

    print("=" * 100)
    print("통합 조회 조건")
    print("=" * 100)
    print(json.dumps(result["query"], ensure_ascii=False, indent=2))

    print("=" * 100)
    print("날씨")
    print("=" * 100)
    print(json.dumps(result["weather"], ensure_ascii=False, indent=2))

    print_section("축제", result["festival"])
    print_section("행사", result["event"])
    print_section("공연", result["performance"])
    print_section("스포츠", result["sports"])

    print("=" * 100)
    print("유동인구")
    print("=" * 100)
    print(json.dumps(result["footfall"].get("summary", {}), ensure_ascii=False, indent=2))
    for idx, item in enumerate(result["footfall"].get("items", []), start=1):
        print(f"[{idx}] {json.dumps(item, ensure_ascii=False)}")

    print("=" * 100)
    print("공휴일/특일")
    print("=" * 100)
    print(json.dumps(result["special_day"], ensure_ascii=False, indent=2))

    if args.output_json:
        path = Path(args.output_json)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장 완료: {path}")


if __name__ == "__main__":
    main()
