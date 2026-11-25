"""Add separate population, quarterly, and radius inventory views."""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

from common import TIME_BANDS
from hongdae_data import _quarter_snapshot


def environments(location, baseline):
    metadata = baseline.get("metadata", {})
    period = metadata.get("source_period")
    dong = metadata.get("administrative_dong_code")
    totals = defaultdict(int)
    industries = {code: {"store_count": count, "estimated_sales": None, "sales_transaction_count": None}
                  for code, count in baseline.get("industry_counts", {}).items()}
    if period and metadata.get("sales_source") == "OA-22175 API":
        path = Path("data/hongdae/quarterly") / f"sales_{period.replace('Q', '')}.json"
        if path.is_file():
            rows = (_quarter_snapshot(path, path.stat().st_mtime_ns) or {}).get("rows", [])
            for row in rows:
                code = str(row["ADSTRD_CD"])
                amount = row.get("THSMON_SELNG_AMT")
                if amount is not None and amount != "":
                    totals[code] += int(amount)
                if code == dong:
                    item = industries.setdefault(row["SVC_INDUTY_CD"], {"store_count": None})
                    count = row.get("THSMON_SELNG_CO")
                    item.update(estimated_sales=int(amount) if amount not in (None, "") else None,
                                sales_transaction_count=int(count) if count not in (None, "") else None,
                                industry_name=row.get("SVC_INDUTY_CD_NM"))
    percentile = None
    if dong in totals and totals[dong] == baseline.get("estimated_sales"):
        target = totals[dong]
        percentile = round(100 * (sum(v < target for v in totals.values())
                                  + .5 * sum(v == target for v in totals.values())) / len(totals), 2)
    structural = {key: baseline.get(key) for key in (
        "estimated_sales", "sales_transaction_count", "total_store_count", "general_store_count",
        "franchise_store_count", "opening_store_count", "opening_rate", "closure_store_count", "closure_rate")}
    structural.update(administrative_dong_code=dong, administrative_dong=location.get("administrative_dong"),
                      source_period=period, source_reference_date=metadata.get("published_at"),
                      sales_percentile=percentile, sales_percentile_population=len(totals) if percentile is not None else None,
                      percentile_method="same_quarter_seoul_dong_midrank", industries=industries,
                      metadata={**metadata, "usage": "quarterly_structural_reference"},
                      source_status=baseline.get("source_status", "no_data"))
    inventory = baseline.get("radius_inventory_reference", {})
    nearby = {key: inventory.get(key) for key in (
        "source_updated_at", "total_store_count", "stores_per_km2", "industry_counts", "industry_share_pct",
        "source", "source_file")}
    nearby.update(latitude=location.get("latitude"), longitude=location.get("longitude"),
                  radius_m=location.get("radius_m"), source_status=inventory.get("source_status", "no_data"),
                  method="distance_filter_and_unique_store_id",
                  usage="dated_structural_reference_not_historical_daily_inventory")
    return structural, nearby


def vitality(result, expected, current, prior, matches):
    matched = {(row["date"], row["time_band"]) for row in matches}
    excluded = []
    for day in expected:
        previous_day = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
        for band in TIME_BANDS:
            if (day, band) in matched:
                continue
            left = current.get(day, {}).get(band, {})
            right = prior.get((previous_day, band), {}).get("dynamic_signals", {}).get("living_population", {})
            excluded.append({"date": day, "previous_date": previous_day, "time_band": band,
                             "current_status": left.get("source_status", "missing"),
                             "previous_status": right.get("source_status", "missing"),
                             "reason": "source_missing_or_incomplete" if left.get("source_status") != "ok"
                             or right.get("source_status") != "ok" else "not_comparable"})
    quality = result["data_quality"]
    previous_mean = mean(row["previous_population"] for row in matches) if matches else None
    return {**quality["weekly_vitality"],
            "status": ("no_data" if not any(current.values()) else quality["weekly_vitality"]["status"]),
            "analysis_period": result["period"],
            "comparison_period": {name: (date.fromisoformat(day) - timedelta(days=7)).isoformat()
                                  for name, day in result["period"].items()},
            "daily_population": result["daily_scores"],
            "excluded_bands": excluded,
            "matched_mean_population_change_pct": round(
                (mean(row["population"] for row in matches) / previous_mean - 1) * 100, 2) if previous_mean else None,
            "full_week_population_change_pct": quality["population_week_over_week_pct"] if not excluded else None,
            **{key: quality[key] for key in ("most_active_population_day", "quietest_population_day",
                                            "strongest_population_band", "weakest_population_band")}}
