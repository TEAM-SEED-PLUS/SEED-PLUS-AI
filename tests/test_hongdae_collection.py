from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from hongdae_population_collect import collect, validate_zip


def fixture_zip(path, value):
    from hongdae_data import PUBLIC_COLUMNS
    fields = [PUBLIC_COLUMNS[k] for k in ("date", "hour", "dong", "grid", "population")]
    rows = [",".join(fields)] + [f"20261121,{hour:02d},11440660,A,{value}" for hour in range(24)]
    rows.append("20261121,00,11440670,A,*")
    with ZipFile(path, "w") as archive:
        archive.writestr("input.csv", "\n".join(rows).encode("cp949"))


def test_collect_dedup_revision_and_failed_replacement(tmp_path):
    sample = tmp_path / "sample.zip"
    fixture_zip(sample, "7.5")
    session = Mock()
    listing = session.get.return_value
    listing.status_code = 200
    listing.text = """<tr><td onclick="downloadFile('261121');">250_LOCAL_RESD_20261121.zip</td><td>2026.11.25</td></tr>"""
    download = session.post.return_value
    download.__enter__ = Mock(return_value=download)
    download.__exit__ = Mock(return_value=False)
    download.status_code = 200
    download.iter_content.return_value = [sample.read_bytes()]
    raw = tmp_path / "raw"
    args = (["2026-11-21", "2026-11-22"],)
    options = dict(raw_dir=raw, manifest_path=tmp_path / "manifest.json", session=session)
    first = collect(*args, **options)
    assert first["statuses"] == {"2026-11-21": "stored", "2026-11-22": "missing"}
    assert collect(*args, **options)["statuses"]["2026-11-21"] == "existing"
    assert session.post.call_count == 1
    listing.text = listing.text.replace("2026.11.25", "2026.11.26")
    fixture_zip(sample, "8.5")
    download.iter_content.return_value = [sample.read_bytes()]
    revised = collect(*args, **options)
    assert revised["files"]["2026-11-21"]["sha256"] != first["files"]["2026-11-21"]["sha256"]
    download.iter_content.return_value = [b"corrupt"]
    assert collect(*args, **options, refresh=["2026-11-21"])["statuses"]["2026-11-21"] == "failed:BadZipFile"
    assert (raw / "250_LOCAL_RESD_20261121.zip").read_bytes() == sample.read_bytes()


def test_invalid_header_rejected(tmp_path):
    path = tmp_path / "invalid.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("input.csv", "day,hour\n20261121,00")
    with pytest.raises(ValueError, match="unexpected_csv_header"):
        validate_zip(path, "2026-11-21")


def test_daily_recalculates_only_when_input_hash_changes(tmp_path, monkeypatch):
    import hongdae_jobs as jobs
    from common import TIME_BANDS

    source = tmp_path / "source.zip"
    fixture_zip(source, "7.5")
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    monkeypatch.setattr(jobs, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(jobs, "population_paths", lambda _: [source])
    population = Mock(dates=["2026-11-21"])
    monkeypatch.setattr(jobs, "configured_population", Mock(return_value=population))
    generate = Mock(side_effect=lambda *a, **kw: {"generated_at": "now", "data_quality": {}})
    monkeypatch.setattr(jobs, "generate_hongdae_market_feed", generate)
    jobs.calculate_days(["2026-11-21"])
    jobs.calculate_days(["2026-11-21"])
    assert generate.call_count == len(TIME_BANDS)
    fixture_zip(source, "8.5")
    jobs.calculate_days(["2026-11-21"])
    assert generate.call_count == 2 * len(TIME_BANDS)


def test_stored_weekly_api_keeps_all_three_sections_without_outbound(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    def blocked(*args, **kwargs):
        pytest.fail("Stored weekly endpoint attempted an outbound request")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    response = TestClient(app).get("/api/v1/hongdae-weekly-briefing",
                                   params={"start": "2026-11-16", "end": "2026-11-22"})
    # Deployments may omit local generated artifacts.
    if response.status_code == 404:
        pytest.skip("Local demo artifact is not installed")
    assert response.status_code == 200
    data = response.json()
    for name in ("weekly_vitality", "structural_environment", "nearby_competition"):
        assert isinstance(data[name], dict)
    assert "structural_baseline" in data and "weekly_average_score" in data
