from pathlib import Path

import pytest

from docker_seed_data import seed_missing, validate_image_seed


def test_seed_empty_mount_then_preserve_runtime_files(tmp_path):
    source, target = tmp_path / "image", tmp_path / "mount"
    (source / "weekly").mkdir(parents=True)
    (source / "weekly" / "example.json").write_text("initial")
    assert seed_missing(source, target) == 1
    saved = target / "weekly" / "example.json"
    assert saved.read_text() == "initial"
    saved.write_text("runtime")
    (target / "collector-state.json").write_text("keep")
    (source / "weekly" / "new.json").write_text("added")
    assert seed_missing(source, target) == 1
    assert saved.read_text() == "runtime"
    assert (target / "collector-state.json").read_text() == "keep"
    assert seed_missing(source, target) == 0


def test_missing_image_seed_fails_without_creating_target(tmp_path):
    target = tmp_path / "mount"
    with pytest.raises(FileNotFoundError, match="Image seed directory missing"):
        seed_missing(tmp_path / "missing", target)
    assert not target.exists()


def test_release_seed_requires_quarter_files(tmp_path):
    with pytest.raises(ValueError, match="sales_20262.json"):
        validate_image_seed(tmp_path)


def test_release_seed_rejects_lfs_pointer_in_place_of_quarter_json(tmp_path):
    from zipfile import ZipFile

    for path in ("raw/spatial/seoul-grid-250m-5179.zip",
                 "raw/living_population/250_LOCAL_RESD_20261121.zip",
                 "raw/stores/example.zip"):
        output = tmp_path / path
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "w") as archive:
            archive.writestr("valid.txt", "fixture")
    quarterly = tmp_path / "hongdae/quarterly"
    quarterly.mkdir(parents=True)
    (quarterly / "sales_20262.json").write_text("version https://git-lfs.github.com/spec/v1\n")
    (quarterly / "stores_20262.json").write_text('{"quarter":"20262","rows":[{}]}')
    weekly = tmp_path / "hongdae/weekly"
    weekly.mkdir()
    (weekly / "2026-11-16_2026-11-22.json").write_text('{"weekly_vitality":{"status":"partial"}}')
    with pytest.raises(ValueError, match="Invalid release seed JSON: .*sales_20262.json"):
        validate_image_seed(tmp_path)
