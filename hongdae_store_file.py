"""SEMAS published Seoul store inventory, used as dated structural context."""
import csv
import math
import re
from collections import Counter
from functools import lru_cache
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile


@lru_cache(maxsize=8)
def _inventory(path, modified, latitude, longitude, radius_m):
    counts, stores = Counter(), {}
    with ZipFile(path) as archive:
        member = next((name for name in archive.namelist()
                       if re.search(r"_서울_\d{6}\.csv$", name)), None)
        if member is None:
            return None
        with TextIOWrapper(archive.open(member), encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    lat, lon = float(row["위도"]), float(row["경도"])
                except (ValueError, TypeError):
                    continue
                if abs(lat - latitude) > radius_m / 110_000 or abs(lon - longitude) > radius_m / 80_000:
                    continue
                a = math.sin(math.radians(lat - latitude) / 2)**2
                a += math.cos(math.radians(lat)) * math.cos(math.radians(latitude)) * math.sin(math.radians(lon - longitude) / 2)**2
                distance = 12_742_000 * math.asin(math.sqrt(a))
                store_id = row["상가업소번호"]
                if distance <= radius_m and store_id and store_id not in stores:
                    industry = row["상권업종대분류명"] or "미분류"
                    counts[industry] += 1
                    stores[store_id] = {"id": store_id, "latitude": lat, "longitude": lon,
                                        "industry": industry, "industry_code": row["상권업종소분류코드"],
                                        "distance_m": round(distance, 1)}
    total = len(stores)
    stamp = re.search(r"(\d{8})\.zip$", path.name)
    day = stamp[1] if stamp else None
    return {"source": "SEMAS published Seoul inventory", "source_file": member, "source_status": "ok",
            "source_updated_at": f"{day[:4]}-{day[4:6]}-{day[6:]}" if day else None,
            "radius_m": radius_m, "total_store_count": total, "industry_counts": dict(counts),
            "industry_share_pct": {name: round(count / total * 100, 2) for name, count in counts.items()},
            "stores_per_km2": round(total / (math.pi * (radius_m / 1000)**2), 1),
            "stores": sorted(stores.values(), key=lambda store: store["distance_m"]),
            "franchise_count": None}


def radius_inventory(location):
    for path in sorted(Path("data/raw/stores").glob("*.zip"), reverse=True):
        result = _inventory(path, path.stat().st_mtime_ns, location.latitude, location.longitude, location.radius_m)
        if result is not None:
            return result
