from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from hongdae_feed_pipeline import generate_hongdae_market_feed, load_hongdae_snapshot
from naver_location_api import NaverLocationClient
from weekly_briefing import aggregate_weekly
from hongdae_data import PopulationFile
from naver_location_api import NaverMapsClient
from zipfile import ZipFile
from hongdae_quarterly_collect import fetch_quarter


def test_population_zip_preserves_dong_parts_for_one_grid(tmp_path):
    path = tmp_path / "input.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("input.csv", "d,h,a,g,p\n20261116,20,123,A,10\n20261116,20,124,A,30\n"
                         "20261116,21,123,A,20\n20261116,22,123,A,20\n20261116,23,123,A,20\n")
    provider = PopulationFile([path], dict(date="d", hour="h", dong="a", grid="g", population="p"),
                              "123", weights={"A": .5})
    result = provider.get("2026-11-16", "저녁", "서교동")
    assert result["spatial_grain"] == "radius_grid_estimate"
    assert result["population"] == 12.5


def test_quarter_pagination_validates_unique_industry_rows(monkeypatch):
    monkeypatch.setenv("SEOUL_DONG_SALES_API_KEY", "unit-token")
    session = Mock()
    pages = []
    for industry in ("A", "B"):
        response = Mock(status_code=200)
        response.json.return_value = {"VwsmAdstrdSelngW": {"list_total_count": 2,
            "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
            "row": [{"STDR_YYQU_CD": "20262", "ADSTRD_CD": "11440660", "SVC_INDUTY_CD": industry}]}}
        pages.append(response)
    session.get.side_effect = pages
    assert len(fetch_quarter("sales", "20262", session=session, page_size=1)) == 2


def test_population_averages_hours_and_deduplicates_cells(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("d,h,a,g,p\n20261116,20,123,A,10\n20261116,20,123,A,10\n"
                    "20261116,20,123,B,20\n20261116,21,123,A,50\n", encoding="utf-8")
    provider = PopulationFile(path, dict(date="d", hour="h", dong="a", grid="g", population="p"), "123")
    result = provider.get("2026-11-16", "저녁", "서교동")
    assert result["population"] == 40
    assert result["source_status"] == "partial"
    assert result["spatial_grain"] == "administrative_dong"


def test_maps_reverse_uses_lon_lat_and_separate_headers(monkeypatch):
    monkeypatch.setenv("NAVER_MAPS_CLIENT_ID", "mock-id")
    monkeypatch.setenv("NAVER_MAPS_CLIENT_SECRET", "mock-secret")
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {"results": [
        {"name": "admcode", "code": {"id": "1144066000"}, "region": {"area3": {"name": "서교동"}}}
    ]}
    result = NaverMapsClient(session).reverse_geocode(37.5563, 126.9236)
    assert result["administrative_dong_code"] == "1144066000"
    assert "mock-secret" not in str(result)
    assert session.get.call_args.kwargs["params"]["coords"] == "126.9236,37.5563"
    assert session.get.call_args.kwargs["headers"]["x-ncp-apigw-api-key-id"] == "mock-id"


class Population:
    def get(self, date, time_band, dong):
        return {"date": date, "time_band": time_band, "administrative_dong": dong,
                "population": 1000, "source_status": "ok", "source_time": date}


class Baseline:
    def get(self, dong):
        return {"estimated_sales": 100_000_000, "total_store_count": 20,
                "industry_counts": {}, "opening_rate": 2.0, "opening_store_count": 2,
                "closure_rate": 3.0, "closure_store_count": 3, "source_status": "ok",
                "metadata": {"source_period": "2025Q4", "grain": "administrative_dong",
                             "baseline_type": "quarterly"}}


def store_client():
    client = Mock()
    client.stores_in_radius.return_value = {"status": "ok", "items": [
        {"bizesId": "A", "indsLclsNm": "음식", "brchNm": ""},
        {"bizesId": "A", "indsLclsNm": "음식", "brchNm": ""},
        {"bizesId": "B", "indsLclsNm": "소매", "brchNm": "홍대점"},
    ]}
    return client


def test_naver_missing_keys_is_safe():
    result = NaverLocationClient("", "").resolve("홍대입구 거리")
    assert result["source_status"] == "no_data"
    assert "secret" not in str(result).lower()


def test_pipeline_is_weather_free_and_calls_radius():
    client = store_client()
    with TemporaryDirectory() as tmp, patch("weather_api.get_weather") as weather:
        result = generate_hongdae_market_feed("2026-12-01", "18:00", store_client=client,
            population_provider=Population(), baseline_provider=Baseline(), data_dir=tmp)
        weather.assert_not_called()
        client.stores_in_radius.assert_called_once_with(37.5563, 126.9236, 300)
        assert "weather" not in result["dynamic_signals"]
        assert "weather" not in result["structural_baseline"]
        assert "weather" not in result["scoring"]["contributions"]
        assert load_hongdae_snapshot("2026-12-01", "오후", tmp) == result
        assert not list(Path(tmp).glob("*.tmp"))


def test_quarterly_language_and_llm_fallback():
    with TemporaryDirectory() as tmp:
        result = generate_hongdae_market_feed("2026-12-01", "18:00", store_client=store_client(),
            population_provider=Population(), baseline_provider=Baseline(), data_dir=tmp)
    text = str(result["narrative"])
    assert "최신 분기" in text
    assert "지난주 매출" not in text and "이번 주 폐업" not in text
    assert result["narrative"]["generation_mode"] == "deterministic_fallback"


def test_weekly_best_worst_and_no_previous_week():
    rows = [
        {"date": "2026-11-30", "time_band": "점심", "opportunity_score": 40,
         "indicators": {}, "location": {}, "structural_baseline": {}},
        {"date": "2026-12-01", "time_band": "저녁", "opportunity_score": 80,
         "indicators": {}, "location": {}, "structural_baseline": {}},
    ]
    result = aggregate_weekly(rows, "2026-11-30", "2026-12-06")
    assert result["best_day"]["score"] == 80
    assert result["worst_day"]["score"] == 40
    assert result["week_over_week"] is None
