"""CLI collector for complete daily sports schedules."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from common import SEOUL_TZ
from snapshot_cache import write_snapshot
from sports_api import (get_kbl_schedule, get_kbo_schedule, get_kleague_schedule,
                        infer_district_from_stadium, parse_target_date, sync_playwright)
from sports_cache import sports_snapshot_path


def collect_sports_schedule(date_str: str | None = None) -> list[dict]:
    target = parse_target_date(date_str)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled",
                                                                  "--disable-dev-shm-usage", "--no-sandbox"])
        try:
            rows = get_kbo_schedule(browser, target) + get_kleague_schedule(browser, target) + get_kbl_schedule(browser, target)
        finally:
            browser.close()
    unique = {}
    for row in rows:
        item = dict(row)
        item["district"] = infer_district_from_stadium(str(item.get("stadium") or ""))
        unique[tuple(str(item.get(k) or "") for k in ("sport", "league", "date", "time", "match", "stadium"))] = item
    return sorted(unique.values(), key=lambda x: (x.get("time") or "", x.get("league") or "", x.get("match") or ""))


def collect_sports_once(date_str: str | None = None, *, data_dir: str | Path | None = None) -> dict:
    target = parse_target_date(date_str).strftime("%Y-%m-%d")
    generated = datetime.now(SEOUL_TZ).isoformat(timespec="seconds")
    try:
        items = collect_sports_schedule(target)
        payload = {"requested_date": target, "source_date": target, "generated_at": generated,
                   "received_at": generated, "source_status": "ok" if items else "empty",
                   "item_count": len(items), "items": items}
    except Exception as exc:
        payload = {"requested_date": target, "source_date": target, "generated_at": generated,
                   "received_at": generated, "source_status": "failed", "item_count": 0,
                   "items": [], "error": type(exc).__name__}
    write_snapshot(sports_snapshot_path(target, data_dir), payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="daily KBO/K League/KBL snapshot collector")
    parser.add_argument("--date", default=None)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    print(json.dumps(collect_sports_once(args.date, data_dir=args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
