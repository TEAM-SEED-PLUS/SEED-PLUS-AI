"""Offline collector for the five daily Seoul district overview snapshots."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Match app.main: environment-backed defaults must be populated before common
# and the production pipeline are imported.
from dotenv import load_dotenv

load_dotenv()

from common import (DISTRICT_KO_TO_EN, SEOUL_TZ, TIME_BAND_REPRESENTATIVE_TIMES,
                    resolve_time_input)
from public_feed_schema import generate_public_market_feed
from snapshot_cache import write_snapshot
from weather_overview_cache import overview_snapshot_path


def collect_weather_overview(date_str: str, time_band: str, *,
                             data_dir: str | Path | None = None,
                             generator: Callable[..., dict[str, Any]] = generate_public_market_feed) -> dict[str, Any]:
    target = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
    representative_time, resolved_band = resolve_time_input(None, time_band)
    districts, failures, no_data_districts = [], [], []
    failure_types: dict[str, int] = {}
    for district in DISTRICT_KO_TO_EN:
        try:
            feed = generator(district=district, date_str=target, time_str=representative_time)
            weather = feed.get("market_weather") or {}
            score = feed.get("opportunity_score")
            if score is None or not weather.get("grade"):
                no_data_districts.append(district)
                continue
            districts.append({"district": district, "opportunity_score": score,
                              "grade": weather.get("grade"), "emoji": weather.get("emoji")})
        except Exception as exc:
            failures.append(district)
            name = type(exc).__name__
            failure_types[name] = failure_types.get(name, 0) + 1
    if len(districts) == 25:
        status = "ok"
    elif districts:
        status = "partial"
    elif len(failures) == 25:
        status = "failed"
    elif failures:
        status = "partial"
    else:
        status = "no_data"
    generated = datetime.now(SEOUL_TZ).isoformat(timespec="seconds")
    payload = {"source_date": target, "time_band": resolved_band,
               "representative_time": representative_time, "generated_at": generated,
               "source_status": status, "district_count": len(districts),
               "districts": districts, "failed_districts": failures,
               "diagnostics": {"attempted_district_count": 25,
                               "no_data_district_count": len(no_data_districts),
                               "failure_types": failure_types}}
    write_snapshot(overview_snapshot_path(target, resolved_band, data_dir), payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Seoul 25-district weather overview collector")
    parser.add_argument("--date", help="YYYY-MM-DD (default: current Seoul date with --active)")
    parser.add_argument("--time-band",
                        choices=list(TIME_BAND_REPRESENTATIVE_TIMES))
    parser.add_argument("--active", action="store_true",
                        help="collect only the current Asia/Seoul date and active time band")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if args.active:
        if args.date or args.time_band:
            parser.error("--active cannot be combined with --date or --time-band")
        current = datetime.now(SEOUL_TZ)
        date_str = current.strftime("%Y-%m-%d")
        _, time_band = resolve_time_input(current.strftime("%H:%M"), None)
    else:
        if not args.date or not args.time_band:
            parser.error("--date and --time-band are required unless --active is used")
        date_str, time_band = args.date, args.time_band
    result = collect_weather_overview(date_str, time_band, data_dir=args.data_dir)
    print(json.dumps({key: value for key, value in result.items() if key != "districts"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
