"""Opt-in latency diagnostic. It prints timings/statuses, never credentials or URLs."""
from __future__ import annotations

import argparse
import time

from common import DEFAULT_PUBLIC_DATA_KEY, DEFAULT_SEOUL_KEY, build_query_context
from event_api import get_events
from festival_api import get_festivals
from footfall_api import get_footfall
from performance_cache import get_cached_performances
from public_feed_schema import generate_public_market_feed
from special_day_api import get_special_day_info
from sports_cache import get_cached_sports
from weather_api import get_weather


def measure(name, call):
    started = time.perf_counter()
    try:
        value = call()
        status = ((value.get("source_status") or {}).get("status") if isinstance(value, dict) else None) or "success"
    except Exception as exc:
        status = f"failed:{type(exc).__name__}"
    print(f"{name:24s} {time.perf_counter() - started:8.3f}s {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weather Feed source/request latency diagnostic")
    parser.add_argument("--district", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--time", required=True)
    args = parser.parse_args()
    ctx = build_query_context(args.district, args.date, args.time)
    calls = [
        ("weather", lambda: get_weather(ctx.district_ko, ctx.date_str, ctx.time_str, DEFAULT_PUBLIC_DATA_KEY)),
        ("festival", lambda: get_festivals(ctx.district_ko, ctx.date_str, ctx.time_str, DEFAULT_PUBLIC_DATA_KEY)),
        ("event", lambda: get_events(ctx.district_ko, ctx.date_str, ctx.time_str, DEFAULT_SEOUL_KEY)),
        ("performance cache read", lambda: get_cached_performances(ctx.district_ko, ctx.date_str, ctx.time_str)),
        ("sports cache read", lambda: get_cached_sports(ctx.district_ko, ctx.date_str, ctx.time_str)),
        ("footfall/cache", lambda: get_footfall(ctx.district_ko, ctx.date_str, ctx.time_str,
                                                DEFAULT_SEOUL_KEY, allow_english_fallback=False)),
        ("special_day", lambda: get_special_day_info(DEFAULT_PUBLIC_DATA_KEY, ctx.date_str)),
        ("total FastAPI pipeline", lambda: generate_public_market_feed(
            district=ctx.district_ko, date_str=ctx.date_str, time_str=ctx.time_str)),
    ]
    for name, call in calls:
        measure(name, call)


if __name__ == "__main__":
    main()
