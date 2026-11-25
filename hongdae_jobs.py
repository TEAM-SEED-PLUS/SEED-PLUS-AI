"""Run Hongdae jobs independently from the serving API."""
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import os

from dotenv import load_dotenv

from common import SEOUL_TZ, TIME_BANDS
from hongdae_data import configured_population, population_paths
from hongdae_feed_pipeline import generate_hongdae_market_feed, snapshot_path
from hongdae_population_collect import collect, sha256, MANIFEST
from weather_overview_scheduler import _collector_file_lock
from snapshot_cache import read_snapshot, write_snapshot
from weekly_briefing import build_weekly_briefing

ROOT = Path("data/hongdae")
DAILY_VERSION = "hongdae_no_weather-v1/population-snapshot-v2"


def calculate_days(days):
    output = ROOT / "backfill"
    manifest = (read_snapshot(MANIFEST) or {}).get("files", {})
    grid = Path(os.getenv("HONGDAE_GRID_FILE") or "data/raw/spatial/seoul-grid-250m-5179.zip")
    grid_hash = sha256(grid) if grid.is_file() else None
    statuses = {}
    for day in days:
        paths = population_paths([day])
        if not paths:
            statuses[day] = "missing"
            continue
        fingerprint = {"algorithm_version": DAILY_VERSION, "raw_sha256": [sha256(path) for path in paths],
                       "grid_sha256": grid_hash, "radius_m": os.getenv("HONGDAE_RADIUS_M") or "300",
                       "columns": os.getenv("HONGDAE_POPULATION_COLUMNS")}
        saved = [read_snapshot(snapshot_path(day, band, output)) for band in TIME_BANDS]
        if all(row and row.get("data_quality", {}).get("calculation_inputs") == fingerprint for row in saved):
            statuses[day] = "unchanged"
            continue
        population = configured_population(days=[day])
        if not population or day not in population.dates:
            statuses[day] = "missing"
            continue
        calendar = read_snapshot(ROOT / "events" / f"{day}.json") or {}
        for band in TIME_BANDS:
            result = generate_hongdae_market_feed(
                day, time_band=band, historical=True, population_provider=population, persist=False,
                special_day_provider=lambda _: calendar.get("special_day", {"source_status": "no_data"}))
            source = manifest.get(day, {})
            collected = source.get("collected_at") if source.get("sha256") in fingerprint["raw_sha256"] else None
            result["data_quality"].update(calculation_inputs=fingerprint, observation_date=day,
                                          collected_at=collected, calculated_at=result["generated_at"])
            write_snapshot(snapshot_path(day, band, output), result)
        statuses[day] = "calculated"
    print(statuses)


def main():
    load_dotenv()
    today = datetime.now(SEOUL_TZ).date()
    prior_monday = today - timedelta(days=today.weekday() + 7)
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=("population", "events", "daily", "weekly", "publish"))
    parser.add_argument("--start", type=date.fromisoformat, default=prior_monday)
    parser.add_argument("--days", type=int, choices=range(1, 32), default=7)
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--refresh", action="append", default=[], type=date.fromisoformat)
    args = parser.parse_args()
    days = [(args.start + timedelta(days=offset)).isoformat() for offset in range(args.days)]
    if args.job == "weekly":
        build_weekly_briefing(days[0], days[-1], daily_dir=ROOT / "backfill", use_llm=args.llm)
        return
    if args.job == "events":
        from special_day_api import get_special_day_info
        key = os.getenv("DATA_GO_KR_SERVICE_KEY")
        for day in days:
            try:
                special = get_special_day_info(key, day) if key else {}
            except Exception:
                special = {}
            observed = bool(special) and not special.get("errors")
            write_snapshot(ROOT / "events" / f"{day}.json", {
                "date": day, "content_status": "no_data",
                "special_day": {"source_status": "ok" if observed else "no_data",
                                "is_special_day": any(item.get("isHoliday") == "Y"
                                                      for item in special.get("items", [])) if observed else False},
            })
        return
    with _collector_file_lock(ROOT / ".population.lock") as acquired:
        if not acquired:
            raise SystemExit("Hongdae population job already running")
        if args.job in ("population", "publish"):
            refresh = [day.isoformat() for day in args.refresh]
            retries = (read_snapshot(MANIFEST) or {}).get("retry_dates", [])[-7:]
            report = collect([*days, *refresh, *retries], refresh=refresh)
            print(report["statuses"])
        if args.job in ("daily", "publish"):
            previous = [(date.fromisoformat(day) - timedelta(days=7)).isoformat() for day in days]
            changed = [day for day, status in report["statuses"].items() if status == "stored"] if args.job == "publish" else []
            calculate_days(sorted(set([*previous, *days, *changed])) if args.job == "publish" else days)
        if args.job == "publish":
            build_weekly_briefing(days[0], days[-1], daily_dir=ROOT / "backfill", use_llm=args.llm)


if __name__ == "__main__":
    main()
