"""Persistent Seoul-time scheduling for the Hongdae file pipeline."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import logging
from pathlib import Path

from common import SEOUL_TZ
from hongdae_jobs import ROOT, calculate_days
from hongdae_population_collect import MANIFEST, collect, sha256
from operations_settings import (HONGDAE_DAILY_AT, HONGDAE_POLL_SECONDS,
                                 HONGDAE_PUBLICATION_LAG_DAYS, HONGDAE_WEEKLY_AT,
                                 HONGDAE_WEEKLY_WEEKDAY)
from snapshot_cache import read_snapshot, write_snapshot
from weather_overview_scheduler import _collector_file_lock
from weekly_briefing import build_weekly_briefing

LOGGER = logging.getLogger(__name__)


def due_slots(now):
    local = now.astimezone(SEOUL_TZ)
    today = local.date()
    daily = today - timedelta(days=local.time() < HONGDAE_DAILY_AT)
    weekly = today - timedelta(days=(today.weekday() - HONGDAE_WEEKLY_WEEKDAY) % 7)
    if weekly == today and local.time() < HONGDAE_WEEKLY_AT:
        weekly -= timedelta(days=7)
    return daily, weekly


def week_start(day):
    return day - timedelta(days=day.weekday() + 7)


def _input_hashes(root, start, end):
    return {path.name: sha256(path) for path in sorted((root / "backfill").glob("*.json"))
            if (start - timedelta(days=7)).isoformat() <= path.name[:10] <= end.isoformat()}


def run_due(now, *, root=ROOT):
    daily, weekly = due_slots(now)
    state_path = root / "scheduler_state.json"
    with _collector_file_lock(root / ".population.lock") as acquired:
        if not acquired:
            return
        state = read_snapshot(state_path) or {}
        attempt = state.get("last_attempt_at")
        if attempt and now - datetime.fromisoformat(attempt) < timedelta(minutes=5):
            return
        if state.get("daily_slot") == daily.isoformat() and state.get("weekly_slot") == weekly.isoformat() and not state.get("pending_weeks"):
            return
        state["last_attempt_at"] = now.isoformat()
        write_snapshot(state_path, state)
        pending = set(state.get("pending_weeks", []))
        if state.get("daily_slot") != daily.isoformat():
            target = daily - timedelta(days=HONGDAE_PUBLICATION_LAG_DAYS)
            last = state.get("daily_slot")
            first = (date.fromisoformat(last) - timedelta(days=HONGDAE_PUBLICATION_LAG_DAYS - 1)
                     if last else target)
            retries = (read_snapshot(MANIFEST) or {}).get("retry_dates", [])[-7:]
            days = sorted(set(retries) | {(first + timedelta(days=i)).isoformat()
                                          for i in range(max(1, (target - first).days + 1))})
            report = collect(days)
            LOGGER.info("Hongdae collection statuses=%s listing_failed=%s",
                        report["statuses"], bool(report.get("listing_error")))
            ready = [day for day, status in report["statuses"].items()
                     if status in ("existing", "stored", "unchanged")]
            # Persist affected weeks before calculating so an interrupted run can reconcile them.
            for day in ready:
                observed = date.fromisoformat(day)
                monday = observed - timedelta(days=observed.weekday())
                for start in (monday, monday + timedelta(days=7)):
                    end = start + timedelta(days=6)
                    if (root / "weekly" / f"{start}_{end}.json").is_file():
                        pending.add(start.isoformat())
            state["pending_weeks"] = sorted(pending)
            write_snapshot(state_path, state)
            calculate_days(ready)
            if not report.get("listing_error") and not any(
                    status.startswith("failed:") for status in report["statuses"].values()):
                state.update(daily_slot=daily.isoformat(), daily_succeeded_at=now.isoformat())
                write_snapshot(state_path, state)
        last_week = state.get("weekly_slot")
        cursor = date.fromisoformat(last_week) + timedelta(days=7) if last_week else weekly
        due = []
        while cursor <= weekly:
            due.append(cursor)
            pending.add(week_start(cursor).isoformat())
            cursor += timedelta(days=7)
        state["pending_weeks"] = sorted(pending)
        write_snapshot(state_path, state)
        if due:
            dates = {(week_start(slot) + timedelta(days=offset)).isoformat()
                     for slot in due for offset in range(-7, 7)}
            calculate_days(sorted(dates))
        published = state.setdefault("published_input_hashes", {})
        for value in sorted(pending):
            start = date.fromisoformat(value)
            end = start + timedelta(days=6)
            hashes = _input_hashes(root, start, end)
            output = root / "weekly" / f"{start}_{end}.json"
            if read_snapshot(output) is None or published.get(value) != hashes:
                build_weekly_briefing(value, end.isoformat(), daily_dir=root / "backfill",
                                      output_dir=root / "weekly")
                published[value] = hashes
                LOGGER.info("Hongdae briefing published start=%s end=%s", start, end)
            state["pending_weeks"].remove(value)
            write_snapshot(state_path, state)
        if due:
            start = week_start(due[-1])
            state.update(weekly_slot=due[-1].isoformat(), weekly_succeeded_at=now.isoformat(),
                         last_analysis_period={"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat()})
            write_snapshot(state_path, state)


async def run():
    LOGGER.info("Hongdae scheduler started timezone=Asia/Seoul daily=%s weekly_day=%s weekly=%s",
                HONGDAE_DAILY_AT, HONGDAE_WEEKLY_WEEKDAY, HONGDAE_WEEKLY_AT)
    while True:
        worker = asyncio.create_task(asyncio.to_thread(run_due, datetime.now(SEOUL_TZ)))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            await asyncio.gather(worker, return_exceptions=True)
            raise
        except Exception as exc:
            LOGGER.error("Hongdae scheduler tick failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(HONGDAE_POLL_SECONDS)
