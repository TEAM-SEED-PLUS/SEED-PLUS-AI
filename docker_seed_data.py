"""Prepare the container data directory before starting the application."""
import os
import json
from pathlib import Path
import shutil
import sys
import tempfile
from zipfile import is_zipfile


def validate_image_seed(source: Path) -> None:
    """Reject incomplete release contexts and Git LFS pointers at build time."""
    required = [
        "raw/spatial/seoul-grid-250m-5179.zip",
        "hongdae/quarterly/sales_20262.json",
        "hongdae/quarterly/stores_20262.json",
        "hongdae/weekly/2026-11-16_2026-11-22.json",
    ]
    missing = [name for name in required if not (source / name).is_file()]
    for pattern in ("raw/living_population/250_LOCAL_RESD_*.zip", "raw/stores/*.zip"):
        if not any(path.is_file() for path in source.glob(pattern)):
            missing.append(pattern)
    if missing:
        raise ValueError("Missing release seed files: " + ", ".join(missing))
    invalid = [str(path.relative_to(source)) for path in source.rglob("*.zip") if not is_zipfile(path)]
    if invalid:
        raise ValueError("Invalid seed ZIPs (run git lfs pull): " + ", ".join(invalid))
    for name in required[1:]:
        try:
            with (source / name).open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError("expected JSON object")
            if "/quarterly/" in name and (str(data.get("quarter")) != "20262" or not data.get("rows")):
                raise ValueError("missing 2026Q2 rows")
            if "/weekly/" in name and not data.get("weekly_vitality"):
                raise ValueError("missing weekly briefing sections")
        except ValueError:
            raise ValueError(f"Invalid release seed JSON: {name} (check git lfs pull)") from None


def seed_missing(source: Path, target: Path) -> int:
    if not source.is_dir():
        raise FileNotFoundError(f"Image seed directory missing: {source}")
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    # Probe write access even when the mount already contains every seed file.
    with tempfile.TemporaryFile(dir=target):
        pass
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        destination = target / path.relative_to(source)
        if destination.exists() or destination.is_symlink():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".seed-", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with path.open("rb") as original:
                    shutil.copyfileobj(original, stream, length=1024 * 1024)
                stream.close()
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    continue
                count += 1
            finally:
                temporary.unlink(missing_ok=True)
    return count


if __name__ == "__main__":
    try:
        count = seed_missing(Path("/opt/seed-data"), Path("/app/data"))
    except OSError as exc:
        print(f"Data initialization failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    print(f"Data initialization complete: {count} initial files copied", flush=True)
    os.execvp(sys.argv[1], sys.argv[1:])
