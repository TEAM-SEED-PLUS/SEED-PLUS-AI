"""Generate historical snapshots from observed population hours."""
import json
import argparse
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from common import TIME_BANDS
from hongdae_data import configured_population
from hongdae_feed_pipeline import generate_hongdae_market_feed
from weekly_briefing import build_weekly_briefing
from snapshot_cache import write_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 16))
    parser.add_argument("--llm", action="store_true")
    args = parser.parse_args()
    start, end = args.start, args.start + timedelta(days=6)
    comparison = start - timedelta(days=7)
    load_dotenv()
    population = configured_population()
    days = [day for day in population.dates if comparison.isoformat() <= day <= end.isoformat()] if population else []
    if population:
        write_snapshot(Path("data/hongdae/population_audit.json"), population.audit)
    daily_dir = "data/hongdae/backfill"
    for day in days:
        for band in TIME_BANDS:
            if population.get(day, band, None)["population"] is not None:
                generate_hongdae_market_feed(day, time_band=band, historical=True,
                                             population_provider=population, data_dir=daily_dir)
    result = build_weekly_briefing(start.isoformat(), end.isoformat(), daily_dir=daily_dir, use_llm=args.llm)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
