"""Explicit source schemas for historical Hongdae inputs."""
import csv
import json
import os
from contextlib import contextmanager
from io import TextIOWrapper
from zipfile import ZipFile
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from statistics import mean
from datetime import datetime

from common import TIME_BANDS
from snapshot_cache import read_snapshot

PUBLIC_COLUMNS = dict(date="일자", hour="시간", dong="행정동코드", grid="250M격자",
                      population="생활인구합계", encoding="cp949", dong_code="11440660")


@contextmanager
def population_stream(path, encoding):
    if Path(path).suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError("Expected one population CSV per ZIP")
            with TextIOWrapper(archive.open(members[0]), encoding=encoding, newline="") as stream:
                yield stream
    else:
        with open(path, encoding=encoding, newline="") as stream:
            yield stream


class PopulationFile:
    def __init__(self, path, columns, dong_code, encoding="utf-8-sig", weights=None):
        self.code = dong_code
        self.weights = weights or {}
        self.suppressed = defaultdict(set)
        cells = {}
        self.audit = {}
        paths = (path,) if isinstance(path, (str, Path)) else path
        for source in paths:
            self._read(source, columns, encoding, cells)
        self.hours = defaultdict(float)
        self.radius_hours = defaultdict(float)
        self.matched = defaultdict(set)
        for (day, hour, dong, grid), value in cells.items():
            if dong == self.code:
                self.hours[day, hour] += value
            if grid in self.weights:
                self.radius_hours[day, hour] += value * self.weights[grid]
                self.matched[day, hour].add(grid)
        self.dates = sorted({day for day, _ in self.hours})

    def _read(self, path, columns, encoding, cells):
        audit = {"rows": 0, "suppressed_total": 0, "duplicates_for_target": 0}
        dates, hours, dongs, grids = set(), set(), set(), set()
        with population_stream(path, encoding) as stream:
            reader = csv.DictReader(stream)
            names = [columns[key] for key in ("date", "hour", "dong", "grid", "population")]
            if not set(names) <= set(reader.fieldnames or []):
                raise ValueError("Population CSV columns do not match the mapping")
            for row in reader:
                day, hour, dong, grid, value = (row[name].strip() for name in names)
                audit["rows"] += 1
                dates.add(day)
                hours.add(hour)
                dongs.add(dong)
                grids.add(grid)
                if value in ("", "*"):
                    audit["suppressed_total"] += 1
                    if grid in self.weights:
                        self.suppressed[day, hour.zfill(2)].add((grid, dong))
                    continue
                if dong != self.code and grid not in self.weights:
                    continue
                day = date.fromisoformat(day if len(day) != 8 else
                                        f"{day[:4]}-{day[4:6]}-{day[6:]}").isoformat()
                hour = int(hour)
                population = float(value)
                if not grid or not 0 <= hour < 24 or not 0 <= population < float("inf"):
                    raise ValueError("Invalid grid-hour observation")
                key = day, hour, dong, grid
                if key in cells and cells[key] != population:
                    raise ValueError("Conflicting duplicate grid-hour")
                if key in cells:
                    audit["duplicates_for_target"] += 1
                cells[key] = population
        self.audit[str(path)] = {**audit, "dates": sorted(dates), "hours": sorted(hours),
                                 "dong_count": len(dongs), "grid_count": len(grids)}

    def get(self, date, time_band, dong):
        start, end = TIME_BANDS[time_band]
        hours = range(start // 60, end // 60)
        radius = bool(self.weights) and all(self.matched.get((date, hour)) == self.weights.keys()
                                            for hour in hours)
        source = self.radius_hours if radius else self.hours
        values = [source[date, hour] for hour in hours if (date, hour) in source]
        absent = sum(len(self.weights) - len(self.matched.get((date, hour), ())) for hour in hours)
        suppressed = sum(len(self.suppressed.get((date.replace("-", ""), f"{hour:02d}"), ())) for hour in hours)
        return {"date": date, "time_band": time_band, "administrative_dong": dong,
                "administrative_dong_code": self.code,
                "spatial_grain": "radius_grid_estimate" if radius else "administrative_dong",
                "fallback_reason": None if radius else ("grid_hour_coverage_incomplete" if self.weights
                                                        else "verified_grid_geometry_unavailable"),
                "intersecting_grid_count": len(self.weights) if self.weights else None,
                "expected_grid_hours": len(self.weights) * len(hours) if self.weights else None,
                "missing_numeric_grid_hours": absent if self.weights else None,
                "suppressed_grid_dong_rows": suppressed,
                "spatial_method": "uniform_population_within_each_grid" if radius else None,
                "source": "서울특별시·KT, 서울시 통신데이터",
                "population_scope": "domestic_residents",
                "hourly_population": {str(hour): round(source[date, hour], 2)
                                      for hour in hours if (date, hour) in source},
                "population": mean(values) if values else None,
                "source_status": ("ok" if len(values) == len(hours) and not suppressed and not absent
                                  else "partial") if values else "no_data",
                "source_time": date if values else None, "observed_hours": len(values),
                "method": "mean_of_hourly_grid_sums",
                "semantics": "시간별 체류 추정인구이며 통행량이나 순방문자 수가 아닙니다."}


@lru_cache(maxsize=4)
def load_population(path, columns, code, modified, weights=()):
    config = json.loads(columns)
    return PopulationFile(path, config, code, config.get("encoding", "utf-8-sig"), dict(weights))


def population_paths(days=None):
    configured = os.getenv("HONGDAE_POPULATION_FILE") or "data/raw/living_population"
    paths = []
    for value in configured.split(os.pathsep):
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(p for p in path.iterdir()
                                if not p.name.startswith(".") and p.suffix.lower() in (".zip", ".csv")))
        elif path.is_file():
            paths.append(path)
    if days is not None:
        stamps = {day.replace("-", "") for day in days}
        paths = [path for path in paths if not path.stem.startswith("250_LOCAL_RESD_")
                 or path.stem.removeprefix("250_LOCAL_RESD_") in stamps]
    return paths


def configured_population(location=None, *, days=None):
    columns = os.getenv("HONGDAE_POPULATION_COLUMNS") or json.dumps(PUBLIC_COLUMNS)
    paths = population_paths(days)
    if paths:
        from hongdae_grid import grid_weights
        from location_context import HONGDAE_DEFAULT
        location = location or HONGDAE_DEFAULT
        radius = int(os.getenv("HONGDAE_RADIUS_M") or location.radius_m) if location is HONGDAE_DEFAULT else location.radius_m
        grid_path = Path(os.getenv("HONGDAE_GRID_FILE") or "data/raw/spatial/seoul-grid-250m-5179.zip")
        weights = {}
        if grid_path.is_file():
            try:
                weights = grid_weights(grid_path, location.latitude, location.longitude, radius)
            except (ImportError, OSError, ValueError, KeyError, StopIteration):
                pass
        config = json.loads(columns)
        return load_population(tuple(paths), columns, config["dong_code"],
                               tuple(path.stat().st_mtime_ns for path in paths), tuple(sorted(weights.items())))


@lru_cache(maxsize=1)
def store_rows():
    root = Path(__file__).resolve().parent / "data/risk/seoul_closure/source"
    from seoul_closure_source_validation import parse_oa22172_zip
    archive = root / "OA-22172_2025.zip"
    rows = parse_oa22172_zip(archive)[0] if archive.is_file() else []
    current = root / "OA-22172_2026Q1.jsonl"
    if current.is_file():
        with current.open(encoding="utf-8") as stream:
            rows.extend(json.loads(line) for line in stream)
    return rows


def quarterly_baseline(location, target_date):
    # Crosswalk verified against the archived Seoul dataset and Maps admcode.
    code = {"1144066000": "11440660"}.get(location.administrative_dong_code)
    target = date.fromisoformat(target_date)
    current_quarter = f"{target.year}{(target.month - 1) // 3 + 1}"
    live = _live_baseline(code, target_date, current_quarter)
    if live:
        rows, sales, live_metadata = live
    else:
        rows = [row for row in store_rows()
                if str(row["ADSTRD_CD"]) == code and str(row["STDR_YYQU_CD"]) < current_quarter]
        sales, live_metadata = {}, {}
    if not rows:
        return None
    quarter = max(str(row["STDR_YYQU_CD"]) for row in rows)
    rows = [row for row in rows if str(row["STDR_YYQU_CD"]) == quarter]
    stores = {row["SVC_INDUTY_CD"]: row for row in rows}
    if len(stores) != len(rows):
        raise ValueError("Duplicate quarterly store industry")
    if not sales and (path := os.getenv("HONGDAE_SALES_FILE")):
        with open(path, encoding=os.getenv("HONGDAE_SALES_ENCODING") or "utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["ADSTRD_CD"] == code and row["STDR_YYQU_CD"] == quarter:
                    industry = row["SVC_INDUTY_CD"]
                    if industry in sales:
                        raise ValueError("Duplicate quarterly sales industry")
                    sales[industry] = row

    def total(field):
        return sum(row[field] for row in rows) if all(row.get(field) is not None for row in rows) else None

    def sales_total(field):
        values = [row.get(field) for row in sales.values()]
        return sum(int(value) for value in values) if values and all(value not in (None, "") for value in values) else None

    count = total("SIMILR_INDUTY_STOR_CO")
    opened, closed = total("OPBIZ_STOR_CO"), total("CLSBIZ_STOR_CO")
    return {"estimated_sales": sales_total("THSMON_SELNG_AMT"),
            "sales_transaction_count": sales_total("THSMON_SELNG_CO"),
            "sales_by_dimension": {field: sales_total(field) for field in {
                field for row in sales.values() for field in row
                if field.endswith(("_SELNG_AMT", "_SELNG_CO"))
                and field.startswith(("MON_", "TUES_", "WED_", "THUR_", "FRI_", "SAT_", "SUN_", "TMZON_",
                                      "MDWK_", "WKEND_", "ML_", "FML_", "AGRDE_"))
            }},
            "total_store_count": count, "general_store_count": total("STOR_CO"),
            "franchise_store_count": total("FRC_STOR_CO"),
            "opening_store_count": opened, "closure_store_count": closed,
            "opening_rate": round(opened / count * 100, 3) if count and opened is not None else None,
            "closure_rate": round(closed / count * 100, 3) if count and closed is not None else None,
            "industry_counts": {code: row.get("SIMILR_INDUTY_STOR_CO") for code, row in stores.items()},
            "matched_industry_count": len(stores.keys() & sales.keys()),
            "source_status": "ok" if sales else "partial",
            "metadata": {"source_period": f"{quarter[:4]}Q{quarter[4]}", "spatial_grain": "administrative_dong",
                         "grain": "administrative_dong", "baseline_type": "quarterly",
                         "administrative_dong_code": code, "collected_at": datetime.now().astimezone().isoformat(),
                         "collected_at_basis": "local_read_time",
                         "fallback": not bool(live_metadata),
                         "stores_source": "OA-22172 archive", "sales_status": "ok" if sales else "no_data",
                         **live_metadata}}


@lru_cache(maxsize=16)
def _quarter_snapshot(path, modified):
    return read_snapshot(path)


def _live_baseline(code, target_date, current_quarter):
    if code is None:
        return None
    root = Path("data/hongdae/quarterly")
    for store_path in sorted(root.glob("stores_*.json"), reverse=True):
        quarter = store_path.stem.split("_", 1)[1]
        sales_path = root / f"sales_{quarter}.json"
        if quarter >= current_quarter or not sales_path.is_file():
            continue
        stores = _quarter_snapshot(store_path, store_path.stat().st_mtime_ns) or {}
        sales = _quarter_snapshot(sales_path, sales_path.stat().st_mtime_ns) or {}
        collected = max(stores.get("collected_at", ""), sales.get("collected_at", ""))
        # Both official dataset pages show the September update for this Q2 release.
        published = "2026-09-11" if quarter == "20262" else None
        if not collected or (published or collected[:10]) > target_date:
            continue
        fields = ("SIMILR_INDUTY_STOR_CO", "STOR_CO", "FRC_STOR_CO", "OPBIZ_STOR_CO", "CLSBIZ_STOR_CO")
        rows = [{**row, **{field: int(row[field]) if row.get(field) not in (None, "") else None
                           for field in fields}} for row in stores.get("rows", [])
                if str(row["ADSTRD_CD"]) == code]
        sales_rows = {row["SVC_INDUTY_CD"]: row for row in sales.get("rows", []) if str(row["ADSTRD_CD"]) == code}
        if rows and sales_rows:
            return rows, sales_rows, {"collected_at": collected, "collected_at_basis": "api_collection",
                                     "published_at": published, "publication_basis": "official_dataset_update"
                                     if published else "first_local_observation",
                                     "stores_source": "OA-22172 API", "sales_source": "OA-22175 API"}
