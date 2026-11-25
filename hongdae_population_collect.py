"""Collect public Seoul living-population archives through their HTTPS form."""
import csv
from datetime import date, datetime
from hashlib import file_digest
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
from zipfile import BadZipFile

import requests

from common import SEOUL_TZ
from hongdae_data import PUBLIC_COLUMNS, population_stream
from snapshot_cache import read_snapshot, write_snapshot

LIST_URL = "https://data.seoul.go.kr/dataList/OA-22784/S/1/datasetView.do"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false"
RAW_DIR = Path("data/raw/living_population")
MANIFEST = Path("data/hongdae/population_collection.json")


class _Listings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = {}
        self.row = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if self.row is not None:
            self.row.extend(value for _, value in attrs if value)

    def handle_data(self, data):
        if self.row is not None:
            self.row.append(data)

    def handle_endtag(self, tag):
        if tag != "tr" or self.row is None:
            return
        row = " ".join(self.row)
        self.row = None
        match = re.search(r"250_LOCAL_RESD_(\d{8})\.zip", row)
        identifier = re.search(r"""downloadFile\(['"](\d+)['"]\)""", row)
        if match and identifier:
            value = match[1]
            day = date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}").isoformat()
            self.files[day] = (identifier[1], " ".join(re.findall(r"\d{4}\.\d{2}\.\d{2}|\d+(?:\.\d+)?\s*(?:MB|KB)", row)))


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def validate_zip(path, day):
    expected = day.replace("-", "")
    hours = set()
    identities = set()
    fields = [PUBLIC_COLUMNS[key] for key in ("date", "hour", "dong", "grid", "population")]
    # Iterating to EOF also checks the ZIP member CRC; preserve the original bytes.
    with population_stream(path, "cp949") as stream:
        reader = csv.DictReader(stream)
        if not set(fields).issubset(reader.fieldnames or ()):
            raise ValueError("unexpected_csv_header")
        for row in reader:
            observed, hour, dong, grid, population = (row[field].strip() for field in fields)
            if observed != expected or not hour.isdecimal() or not 0 <= int(hour) <= 23 or not dong or not grid:
                raise ValueError("invalid_observation")
            identity = (int(hour), dong, grid)
            if identity in identities:
                raise ValueError("duplicate_grid_dong_hour")
            identities.add(identity)
            hours.add(int(hour))
            if population not in ("", "*") and not 0 <= float(population) < float("inf"):
                raise ValueError("invalid_population")
    if hours != set(range(24)):
        raise ValueError("incomplete_hours")


def collect(days, *, raw_dir=RAW_DIR, manifest_path=MANIFEST, session=None, refresh=()):
    session = session or requests.Session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    previous = read_snapshot(manifest_path) or {}
    files = previous.get("files", {})
    statuses = {}
    try:
        response = session.get(LIST_URL, timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("listing_http_error")
        listing = _Listings()
        listing.feed(response.text)
        if not listing.files:
            raise ValueError("listing_unrecognized")
        listing_error = None
    except (requests.RequestException, ValueError) as exc:
        listing = _Listings()
        listing_error = type(exc).__name__
    for day in sorted(set(days)):
        target = raw_dir / f"250_LOCAL_RESD_{day.replace('-', '')}.zip"
        entry = files.get(day, {})
        remote = listing.files.get(day)
        revision = list(remote) if remote else None
        if target.is_file() and day not in refresh and (not remote or not entry.get("revision") or entry["revision"] == revision):
            if remote and not entry.get("revision"):
                files[day] = {**entry, "revision": revision, "sha256": sha256(target)}
            statuses[day] = "existing"
            continue
        if not remote:
            statuses[day] = "listing_failed" if listing_error else "missing"
            continue
        temp = None
        try:
            with session.post(DOWNLOAD_URL, data={"infId": "OA-22784", "infSeq": "1", "seqNo": "", "seq": remote[0]},
                              headers={"Referer": LIST_URL}, timeout=(10, 60),
                              stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError("download_http_error")
                with tempfile.NamedTemporaryFile(dir=raw_dir, suffix=".zip", prefix=".incoming-", delete=False) as stream:
                    temp = Path(stream.name)
                    total = 0
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > 64 * 1024 * 1024:
                            raise ValueError("download_too_large")
                        stream.write(chunk)
            validate_zip(temp, day)
            digest = sha256(temp)
            unchanged = target.is_file() and sha256(target) == digest
            if not unchanged:
                temp.replace(target)
            collected_at = datetime.now(SEOUL_TZ).isoformat()
            files[day] = {"revision": revision, "sha256": digest,
                          "collected_at": entry.get("collected_at") if unchanged else collected_at,
                          "checked_at": collected_at}
            statuses[day] = "unchanged" if unchanged else "stored"
        except (requests.RequestException, OSError, ValueError, TypeError, AttributeError, BadZipFile, csv.Error) as exc:
            statuses[day] = f"failed:{type(exc).__name__}"
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    retry = set(previous.get("retry_dates", [])) - statuses.keys()
    retry.update(day for day, status in statuses.items() if status not in ("stored", "unchanged", "existing"))
    report = {"files": files, "statuses": statuses, "listing_error": listing_error,
              "retry_dates": sorted(retry), "checked_at": datetime.now(SEOUL_TZ).isoformat()}
    write_snapshot(manifest_path, report)
    return report
