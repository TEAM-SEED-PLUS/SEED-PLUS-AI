import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from common import SEOUL_TZ
from oa21285_api import parse_oa21285_response
from oa21285_cache import collect_oa21285_places, load_cached_place, save_cached_place
from oa21285_mapping import generate_spatial_mapping
from oa21285_aggregation import (
    OA21285AggregationPolicy,
    aggregate_district_populations,
    district_coverage_diagnostics,
    preview_aggregation_experiments,
    production_aggregation_state,
)
from oa21285_config import OA21285_CATEGORY_WEIGHTS, OA21285_EXCLUDED_CATEGORIES, OA21285_FOOTFALL_ENABLED
from oa21285_history import save_history_snapshot, summarize_district_history, summarize_district_percentile_history
from oa21285_footfall import (adapt_oa_history_to_footfall, combine_indicator_footfall_sources,
                             select_footfall_provider, load_available_oa_footfall,
                             resolve_indicator_footfall_sources)
from oa21285_percentile import compute_district_population_percentiles
from oa21285_pilot import list_history_snapshots, run_pilot_once
from oa21285_calibration import build_calibration_diagnostics
from oa21285_collector import run_collection_once
from oa21285_monitor import build_monitoring_diagnostics
from indicator_engine import calculate_indicators
from normalized_city_data import _normalize_footfall
from llm_feed_writer import build_llm_context


class FakeResponse:
    def __init__(self, payload=None, *, text="", status=200):
        self.payload = payload
        self.text = text
        self.status_code = status

    def json(self):
        if self.payload is None:
            raise requests.JSONDecodeError("invalid", self.text, 0)
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def normal_payload(*, live=True, forecast_count=12, area_nm="강남역"):
    population = []
    if live:
        population = [{
            "AREA_CD": "POI014", "AREA_NM": area_nm,
            "AREA_PPLTN_MIN": "68000", "AREA_PPLTN_MAX": "70000",
            "AREA_CONGEST_LVL": "약간 붐빔", "AREA_CONGEST_MSG": "혼잡 메시지",
            "PPLTN_TIME": "2026-08-22 16:50", "REPLACE_YN": "N", "FCST_YN": "Y",
            "FCST_PPLTN": [{
                "FCST_TIME": f"2026-08-22 {18 + index:02d}:00",
                "FCST_PPLTN_MIN": str(66000 - index * 1000),
                "FCST_PPLTN_MAX": str(68000 - index * 1000),
                "FCST_CONGEST_LVL": "보통",
            } for index in range(forecast_count)],
        }]
    return {
        "RESULT": {"RESULT.CODE": "INFO-000", "RESULT.MESSAGE": "정상 처리되었습니다."},
        "CITYDATA": {"AREA_CD": "POI014", "AREA_NM": area_nm, "LIVE_PPLTN_STTS": population},
    }


class OA21285ApiTests(unittest.TestCase):
    def test_info_000_normal_and_numeric_conversion(self):
        result = parse_oa21285_response(FakeResponse(normal_payload()), "POI014")
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual(result["population"], {"min": 68000, "max": 70000})
        self.assertEqual(result["replace_yn"], "N")

    def test_info_000_empty_population_is_no_data(self):
        result = parse_oa21285_response(FakeResponse(normal_payload(live=False)), "POI014")
        self.assertEqual(result["source_status"], "no_data")

    def test_error_500_is_failed(self):
        payload = {"RESULT": {"RESULT.CODE": "ERROR-500", "RESULT.MESSAGE": "서버 오류"}}
        result = parse_oa21285_response(FakeResponse(payload), "POI014")
        self.assertEqual(result["source_status"], "failed")
        self.assertEqual(result["result_code"], "ERROR-500")

    def test_xml_info_100_is_failed(self):
        xml = "<RESULT><CODE>INFO-100</CODE><MESSAGE>인증키가 유효하지 않습니다.</MESSAGE></RESULT>"
        result = parse_oa21285_response(FakeResponse(None, text=xml), "POI014")
        self.assertEqual(result["source_status"], "failed")
        self.assertEqual(result["result_code"], "INFO-100")

    def test_population_time_and_twelve_forecasts_are_preserved_separately(self):
        result = parse_oa21285_response(FakeResponse(normal_payload()), "POI014")
        self.assertEqual(result["population_time"], "2026-08-22 16:50")
        self.assertEqual(len(result["forecast"]), 12)
        self.assertNotIn("forecast", result["population"])
        self.assertEqual(result["forecast"][0]["population_min"], 66000)

    def test_reference_live_name_mismatch_diagnostics(self):
        result = parse_oa21285_response(
            FakeResponse(normal_payload(area_nm="실제 이름")), "POI014",
            reference_place={"area_nm": "공식 이름"},
        )
        self.assertTrue(result["diagnostics"]["name_mismatch"])
        self.assertEqual(result["diagnostics"]["reference_name"], "공식 이름")
        self.assertEqual(result["diagnostics"]["live_name"], "실제 이름")


class Box:
    def __init__(self, xmin, ymin, xmax, ymax):
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax

    @property
    def area(self):
        return max(0, self.xmax - self.xmin) * max(0, self.ymax - self.ymin)

    def intersection(self, other):
        return Box(max(self.xmin, other.xmin), max(self.ymin, other.ymin),
                   min(self.xmax, other.xmax), min(self.ymax, other.ymax))


def box_factory(value):
    return Box(*value["box"])


def feature(props, box):
    return {"type": "Feature", "properties": props, "geometry": {"box": box}}


class OA21285MappingTests(unittest.TestCase):
    def setUp(self):
        self.districts = [
            feature({"district": "가구"}, [0, 0, 10, 10]),
            feature({"district": "나구"}, [10, 0, 20, 10]),
        ]

    def test_single_district_mapping_and_ratio(self):
        result = generate_spatial_mapping(
            [feature({"AREA_CD": "POI001", "AREA_NM": "단일"}, [1, 1, 3, 3])],
            self.districts, geometry_factory=box_factory,
        )["mappings"][0]
        self.assertTrue(result["single_district"])
        self.assertFalse(result["cross_boundary"])
        self.assertEqual(result["district_candidates"][0]["intersection_ratio"], 1.0)

    def test_cross_boundary_preserves_all_candidates_and_ratios(self):
        result = generate_spatial_mapping(
            [feature({"AREA_CD": "POI002", "AREA_NM": "경계"}, [8, 0, 12, 10])],
            self.districts, geometry_factory=box_factory,
        )["mappings"][0]
        self.assertTrue(result["cross_boundary"])
        self.assertFalse(result["single_district"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual([item["intersection_ratio"] for item in result["district_candidates"]], [0.5, 0.5])
        self.assertIsNone(result["assigned_district"])
        self.assertEqual(result["assignment_status"], "ambiguous_tie")

    def test_cross_boundary_assigns_only_largest_effective_intersection(self):
        result = generate_spatial_mapping(
            [feature({"AREA_CD": "POI014", "AREA_NM": "강남역"}, [6.648, 0, 16.648, 10])],
            self.districts, geometry_factory=box_factory,
        )["mappings"][0]
        self.assertEqual(result["assigned_district"], "나구")
        self.assertEqual(result["assignment_status"], "assigned")

    def test_unmatched(self):
        result = generate_spatial_mapping(
            [feature({"AREA_CD": "POI003", "AREA_NM": "외부"}, [30, 30, 31, 31])],
            self.districts, geometry_factory=box_factory,
        )["mappings"][0]
        self.assertTrue(result["unmatched"])
        self.assertTrue(result["out_of_service_area"])
        self.assertEqual(result["candidate_count"], 0)

    def test_invalid_geometry_is_diagnostic_not_exception(self):
        def broken_factory(value):
            if value.get("broken"):
                raise ValueError("bad geometry")
            return box_factory(value)
        result = generate_spatial_mapping(
            [{"properties": {"AREA_CD": "POI_BAD"}, "geometry": {"broken": True}}],
            self.districts, geometry_factory=broken_factory,
        )["mappings"][0]
        self.assertEqual(result["assignment_status"], "invalid_geometry")
        self.assertIn("bad geometry", result["geometry_diagnostics"]["error"])

    def test_sub_one_percent_intersection_is_preserved_as_ignored_sliver(self):
        result = generate_spatial_mapping(
            [feature({"AREA_CD": "POI004", "AREA_NM": "슬리버"}, [0, 0, 10.05, 10])],
            self.districts, geometry_factory=box_factory,
        )["mappings"][0]
        self.assertEqual(len(result["raw_candidates"]), 2)
        self.assertEqual(len(result["effective_candidates"]), 1)
        self.assertEqual(result["ignored_sliver_candidates"][0]["district"], "나구")
        self.assertTrue(result["single_district_candidate"])

    def test_ninety_nine_percent_single_tolerance_and_material_cross_boundary(self):
        almost_single = generate_spatial_mapping(
            [feature({"AREA_CD": "POI005"}, [0, 0, 10.1, 10])], self.districts,
            geometry_factory=box_factory,
        )["mappings"][0]
        material_cross = generate_spatial_mapping(
            [feature({"AREA_CD": "POI006"}, [5, 0, 15, 10])], self.districts,
            geometry_factory=box_factory,
        )["mappings"][0]
        self.assertTrue(almost_single["single_district_candidate"])
        self.assertFalse(almost_single["cross_boundary"])
        self.assertTrue(material_cross["cross_boundary"])


class OA21285AggregationTests(unittest.TestCase):
    def mappings(self):
        return [{
            "area_cd": "POI014", "area_nm": "강남역", "category": "인구밀집지역", "out_of_service_area": False,
            "effective_candidates": [
                {"district": "강남구", "intersection_ratio": 0.66},
                {"district": "서초구", "intersection_ratio": 0.34},
            ],
        }, {
            "area_cd": "POI999", "area_nm": "관광", "category": "관광특구", "out_of_service_area": False,
            "effective_candidates": [{"district": "강남구", "intersection_ratio": 1.0}],
        }]

    def test_policy_and_production_footfall_provider_are_enabled(self):
        policy = OA21285AggregationPolicy()
        state = production_aggregation_state(policy)
        self.assertEqual(policy.status, "official_v1")
        self.assertTrue(state["production_enabled"])
        self.assertTrue(OA21285_FOOTFALL_ENABLED)
        self.assertIsNone(state["district_values"])

    def test_coverage_diagnostics_has_all_status_counts(self):
        diagnostics = district_coverage_diagnostics(self.mappings(), [
            {"area_cd": "POI014", "source_status": "ok"},
            {"area_cd": "POI999", "source_status": "failed"},
        ])
        gangnam = diagnostics["강남구"]
        self.assertEqual(gangnam["expected_poi_count"], 2)
        self.assertEqual(gangnam["ok_poi_count"], 1)
        self.assertEqual(gangnam["failed_poi_count"], 1)
        self.assertEqual(gangnam["coverage_ratio"], 0.5)

    def test_midpoint_population_and_population_category_weighted_aggregation(self):
        result = preview_aggregation_experiments("강남구", self.mappings(), [
            {"area_cd": "POI014", "source_status": "ok", "population": {"min": 100, "max": 200}},
            {"area_cd": "POI999", "source_status": "ok", "population": {"min": 200, "max": 300}},
        ])
        expected = (150 * 150 + 250 * 175) / (150 + 175)
        self.assertTrue(result["production_enabled"])
        self.assertEqual(result["official_value"], round(expected, 2))
        self.assertEqual(result["samples"][0]["representative_population"], 150)
        self.assertEqual(result["samples"][1]["category_weight"], 0.7)
        self.assertEqual(result["samples"][1]["final_weight"], 175)

    def test_category_policy_and_exclusions(self):
        self.assertEqual(OA21285_CATEGORY_WEIGHTS, {"발달상권": 1.0, "인구밀집지역": 1.0, "관광특구": 0.7})
        self.assertEqual(OA21285_EXCLUDED_CATEGORIES, {"고궁·문화유산", "공원"})
        mappings = [
            {"area_cd": "P1", "category": "공원", "assigned_district": "강남구"},
            {"area_cd": "P2", "category": "고궁·문화유산", "assigned_district": "강남구"},
        ]
        result = aggregate_district_populations(mappings, [
            {"area_cd": "P1", "source_status": "ok", "population": {"min": 1, "max": 2}},
            {"area_cd": "P2", "source_status": "ok", "population": {"min": 1, "max": 2}},
        ])
        self.assertNotIn("강남구", result["districts"])
        self.assertEqual(len(result["diagnostics"]["excluded_pois"]), 2)

    def test_no_usable_poi_is_explicit_null_no_data(self):
        result = aggregate_district_populations(self.mappings(), [
            {"area_cd": "POI014", "source_status": "failed"},
            {"area_cd": "POI999", "source_status": "no_data"},
        ])
        self.assertIsNone(result["districts"]["강남구"]["district_population"])
        self.assertEqual(result["districts"]["강남구"]["source_status"], "no_data")

    def test_existing_sdot_indicator_fields_keep_their_scores(self):
        empty = {"count": 0, "items": []}
        data = {
            "query": {}, "weather": {}, "special_day": dict(empty),
            "festival": dict(empty), "event": dict(empty), "performance": dict(empty), "sports": dict(empty),
            "overview": {"content_tag_counts": {}},
            "source_status": {"weather": {"status": "failed"}},
            "footfall": {"summary": {
                "visitor_avg_in_band": 100,
                "visitor_max_in_band": 200,
                "visitor_spike_ratio": 2,
            }, "items": []},
        }
        result = calculate_indicators(data)
        self.assertEqual(result.inflow_pressure, 52)
        self.assertEqual(result.spending_intent, 34)
        self.assertEqual(result.competition_pressure, 27)


class OA21285CacheTests(unittest.TestCase):
    def result(self):
        return parse_oa21285_response(FakeResponse(normal_payload()), "POI014")

    def test_cache_save_load_and_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 22, 17, 0, tzinfo=SEOUL_TZ)
            save_cached_place(self.result(), cache_dir=directory, received_at=now)
            fresh = load_cached_place("POI014", cache_dir=directory, now=now + timedelta(minutes=4))
            expired = load_cached_place("POI014", cache_dir=directory, now=now + timedelta(minutes=6))
            self.assertTrue(fresh["cache_fresh"])
            self.assertFalse(expired["cache_fresh"])
            self.assertEqual(fresh["population_time"], "2026-08-22 16:50")
            self.assertEqual(fresh["current"]["population"], {"min": 68000, "max": 70000})
            self.assertEqual(len(fresh["forecast"]), 12)
            self.assertEqual(fresh["age_minutes"], 4.0)
            self.assertEqual(fresh["received_age_minutes"], 4.0)
            self.assertEqual(fresh["population_age_minutes"], 14.0)

    def test_api_key_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_cached_place(self.result(), cache_dir=directory)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("serviceKey", text)
            self.assertNotIn("api_key", text)

    def test_batch_deduplicates_and_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 22, 17, 0, tzinfo=SEOUL_TZ)
            client = Mock()
            client.get_place.return_value = self.result()
            places = [{"area_cd": "POI014", "area_nm": "강남역"}] * 2
            first = collect_oa21285_places(places, client=client, cache_dir=directory, now=now)
            second = collect_oa21285_places(places, client=client, cache_dir=directory, now=now + timedelta(minutes=4))
            self.assertEqual(first["api_call_count"], 1)
            self.assertEqual(second["api_call_count"], 0)
            self.assertEqual(second["cache_hit_count"], 1)
            self.assertEqual(client.get_place.call_count, 1)


class OA21285HistoryAndAdapterTests(unittest.TestCase):
    @staticmethod
    def snapshot(population_time, population, max_reference):
        return [{"area_cd": "POI014", "area_nm": "강남역", "source_status": "ok",
                 "population_time": population_time, "population": {"min": population - 10, "max": population + 10},
                 "replace_yn": "Y", "forecast": [{"population_min": 999999}]}], {
            "policy_status": "official_v1", "districts": {"강남구": {
                "district_population": population, "district_max_population_reference": max_reference,
                "source_status": "ok", "poi_count": 1, "contributions": [],
            }}}

    def test_history_save_duplicate_prevention_and_no_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            places, aggregated = self.snapshot("2026-08-22 17:00", 100, 120)
            mappings = [{"area_cd": "POI014", "assigned_district": "강남구", "category": "인구밀집지역",
                         "effective_candidates": []}]
            path, created = save_history_snapshot(places, mappings, aggregated, history_dir=directory)
            same_path, duplicate_created = save_history_snapshot(places, mappings, aggregated, history_dir=directory)
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(path, same_path)
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn("api_key", saved)
            self.assertNotIn("forecast", saved)
            self.assertEqual(json.loads(saved)["poi_current"][0]["replace_yn"], "Y")

    def test_time_band_percentile_summary_and_oa_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            mappings = [{"area_cd": "POI014", "assigned_district": "강남구", "effective_candidates": []}]
            for time, value, maximum in (("17:00", 100, 120), ("17:05", 200, 240),
                                         ("17:10", 300, 360), ("17:15", 400, 480)):
                places, aggregated = self.snapshot(f"2026-08-22 {time}", value, maximum)
                save_history_snapshot(places, mappings, aggregated, history_dir=directory)
            summary = summarize_district_history("강남구", "2026-08-22", "오후", history_dir=directory)
            self.assertEqual(summary["visitor_avg_in_band"], 250)
            self.assertEqual(summary["visitor_max_in_band"], 400)
            self.assertEqual(summary["visitor_min_in_band"], 100)
            percentile_summary = summarize_district_percentile_history(
                "강남구", "2026-08-22", "오후", history_dir=directory
            )
            self.assertEqual(percentile_summary["snapshot_count"], 4)
            self.assertEqual(percentile_summary["population_percentile_avg_in_band"], 50)
            self.assertEqual(percentile_summary["population_spike_ratio"], 1.6)
            adapted = adapt_oa_history_to_footfall(percentile_summary)
            self.assertEqual(adapted["provider"], "oa21285")
            self.assertNotIn("visitor_avg_in_band", adapted["summary"])
            self.assertEqual(adapted["contributions"]["inflow_level_bonus"], 11)
            self.assertEqual(adapted["contributions"]["inflow_peak_bonus"], 4)
            self.assertEqual(adapted["contributions"]["spending_level_bonus"], 5)
            self.assertNotIn("competition_spike_bonus", adapted["contributions"])
            self.assertEqual(adapted["diagnostics"]["oa_temporal_spike_ratio"], 1.6)
            self.assertFalse(adapted["diagnostics"]["used_for_competition"])

    def test_insufficient_or_failed_oa_falls_back_to_sdot(self):
        sdot = {"summary": {"visitor_avg_in_band": 77}, "source_status": {"status": "ok"}}
        with tempfile.TemporaryDirectory() as directory, patch("oa21285_footfall.OA21285_FOOTFALL_ENABLED", True):
            selected = select_footfall_provider("강남구", "2026-08-22", "오후", sdot, history_dir=directory)
            self.assertEqual(selected["summary"]["visitor_avg_in_band"], 77)
            self.assertEqual(selected["source_status"]["provider"], "sdot")

    def test_real_pilot_evening_history_meets_four_snapshot_adapter_boundary(self):
        gangnam = summarize_district_percentile_history("강남구", "2026-08-22", "저녁")
        jungnang = summarize_district_percentile_history("중랑구", "2026-08-22", "저녁")
        self.assertGreaterEqual(gangnam["snapshot_count"], 4)
        self.assertEqual(len(set(gangnam["source_timestamps"])), gangnam["snapshot_count"])
        self.assertIsNotNone(adapt_oa_history_to_footfall(gangnam))
        self.assertEqual(jungnang["source_status"], "no_data")
        self.assertIsNone(adapt_oa_history_to_footfall(jungnang))


class OA21285PilotCollectorTests(unittest.TestCase):
    def references(self, directory):
        places = Path(directory) / "places.json"
        mappings = Path(directory) / "mappings.json"
        places.write_text(json.dumps({"places": [
            {"area_cd": "POI001", "area_nm": "A", "category": "발달상권"},
            {"area_cd": "POI002", "area_nm": "B", "category": "관광특구"},
        ]}), encoding="utf-8")
        mappings.write_text(json.dumps({"mappings": [
            {"area_cd": "POI001", "area_nm": "A", "category": "발달상권", "out_of_service_area": False,
             "effective_candidates": [{"district": "강남구", "intersection_ratio": 1.0}]},
            {"area_cd": "POI002", "area_nm": "B", "category": "관광특구", "out_of_service_area": False,
             "effective_candidates": [{"district": "종로구", "intersection_ratio": 1.0}]},
        ]}), encoding="utf-8")
        return places, mappings

    @staticmethod
    def result(code, population_time):
        return {"area_cd": code, "area_nm": code, "source_status": "ok", "population_time": population_time,
                "population": {"min": 100, "max": 200}, "replace_yn": "N", "forecast": []}

    def test_probe_new_time_collects_each_unique_poi_once_then_unchanged_skips_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            places, mappings = self.references(directory)
            client = Mock()
            client.get_place.side_effect = [self.result("POI001", "2026-08-22 20:30"),
                                            self.result("POI002", "2026-08-22 20:30")]
            first = run_pilot_once(client=client, probe_poi="POI001", history_dir=directory,
                                   cache_dir=Path(directory) / "cache", places_path=places, mappings_path=mappings)
            self.assertEqual(first["status"], "collected")
            self.assertEqual(first["api_call_count"], 2)
            self.assertEqual(client.get_place.call_count, 2)
            self.assertEqual(list_history_snapshots(directory)[0]["api_call_count"], 2)

            unchanged = Mock()
            unchanged.get_place.return_value = self.result("POI001", "2026-08-22 20:30")
            second = run_pilot_once(client=unchanged, probe_poi="POI001", history_dir=directory,
                                    cache_dir=Path(directory) / "cache", places_path=places, mappings_path=mappings)
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(second["api_call_count"], 1)
            unchanged.get_place.assert_called_once()

    def test_production_collector_failure_is_isolated(self):
        with patch("oa21285_collector.run_pilot_once", side_effect=RuntimeError("OA unavailable")):
            result = run_collection_once()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["batch_skipped"])

    def test_date_and_time_band_history_never_mix_for_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            mappings = [{"area_cd": "POI014", "assigned_district": "강남구", "effective_candidates": []}]
            for day, band_hour, count in (("2026-08-22", 20, 4), ("2026-08-23", 20, 3), ("2026-08-23", 17, 3)):
                for index in range(count):
                    places = [{"area_cd": "POI014", "area_nm": "강남역", "source_status": "ok",
                               "population_time": f"{day} {band_hour}:{index * 5:02d}",
                               "population": {"min": 100 + index, "max": 200 + index}}]
                    aggregated = {"districts": {
                        "강남구": {"district_population": 150 + index, "source_status": "ok", "contributions": []}
                    }}
                    save_history_snapshot(places, mappings, aggregated, history_dir=directory)
            yesterday = summarize_district_percentile_history("강남구", "2026-08-22", "저녁", history_dir=directory)
            today_evening = summarize_district_percentile_history("강남구", "2026-08-23", "저녁", history_dir=directory)
            today_afternoon = summarize_district_percentile_history("강남구", "2026-08-23", "오후", history_dir=directory)
            self.assertIsNotNone(adapt_oa_history_to_footfall(yesterday))
            self.assertIsNone(adapt_oa_history_to_footfall(today_evening))
            self.assertIsNone(adapt_oa_history_to_footfall(today_afternoon))

    def test_monitoring_reports_24_oa_and_jungnang_fallback(self):
        diagnostics = build_monitoring_diagnostics("2026-08-22", "저녁")
        providers = [value["provider"]["inflow"] for value in diagnostics["districts"].values()]
        self.assertEqual(providers.count("oa21285"), 24)
        self.assertEqual(diagnostics["districts"]["중랑구"]["provider"]["inflow"], "sdot")
        self.assertIn("중랑구", diagnostics["no_data_districts"])


class OA21285CalibrationTests(unittest.TestCase):
    def test_percentile_average_rank_ties_and_no_data(self):
        ranked = compute_district_population_percentiles({
            "A": {"district_population": 10}, "B": {"district_population": 20},
            "C": {"district_population": 20}, "D": {"district_population": 100},
            "중랑구": {"district_population": None},
        })
        self.assertEqual(ranked["A"]["population_percentile"], 0)
        self.assertEqual(ranked["B"]["population_percentile"], 50)
        self.assertEqual(ranked["C"]["population_percentile"], 50)
        self.assertEqual(ranked["D"]["population_percentile"], 100)
        self.assertIsNone(ranked["중랑구"]["population_percentile"])
        self.assertEqual(ranked["중랑구"]["source_status"], "no_data")

    def test_minmax_percentile_and_no_data_remain_diagnostics_only(self):
        snapshot = {"population_time": "2026-08-22 20:30", "districts": {
            "강남구": {"district_population": 10}, "마포구": {"district_population": 20},
            "양천구": {"district_population": 100},
        }}
        with tempfile.TemporaryDirectory() as directory:
            result = build_calibration_diagnostics(snapshot, history_dir=directory)
        self.assertEqual(result["policy_status"], "diagnostics_only")
        self.assertEqual(result["districts"]["강남구"]["minmax_0_100"], 0)
        self.assertEqual(result["districts"]["마포구"]["percentile_0_100"], 50)
        self.assertEqual(result["districts"]["양천구"]["minmax_0_100"], 100)
        self.assertEqual(result["districts"]["중랑구"]["source_status"], "no_data")
        self.assertIsNone(result["districts"]["중랑구"]["minmax_0_100"])
        self.assertNotIn("visitor_avg_in_band", result["districts"]["강남구"])


class OA21285ProviderAwareIndicatorTests(unittest.TestCase):
    @staticmethod
    def summary(percentile, *, count=4, spike=1.0, raw=99999999):
        return {"district": "강남구", "time_band": "저녁", "snapshot_count": count,
                "source_status": "ok", "population_raw_current": raw,
                "population_raw_avg_in_band": raw, "population_raw_max_in_band": raw,
                "population_raw_min_in_band": raw, "population_percentile_avg_in_band": percentile,
                "population_percentile_max_in_band": percentile,
                "population_percentile_min_in_band": percentile,
                "population_spike_ratio": spike, "source_timestamps": [str(x) for x in range(count)]}

    @staticmethod
    def indicator_data(footfall):
        empty = {"count": 0, "items": []}
        return {"query": {}, "weather": {}, "special_day": dict(empty), "festival": dict(empty),
                "event": dict(empty), "performance": dict(empty), "sports": dict(empty),
                "overview": {"content_tag_counts": {}}, "source_status": {}, "footfall": footfall}

    def test_percentile_bonus_boundaries_and_caps(self):
        for percentile, expected in ((0, (0, 0, 0)), (50, (11, 4, 5)), (100, (22, 8, 10))):
            with self.subTest(percentile=percentile):
                adapted = adapt_oa_history_to_footfall(self.summary(percentile))
                c = adapted["contributions"]
                self.assertEqual((c["inflow_level_bonus"], c["inflow_peak_bonus"], c["spending_level_bonus"]), expected)

    def test_raw_population_never_enters_sdot_log_formula_and_risk_unchanged(self):
        low_raw = adapt_oa_history_to_footfall(self.summary(50, raw=1))
        high_raw = adapt_oa_history_to_footfall(self.summary(50, raw=10**12))
        low = calculate_indicators(self.indicator_data(low_raw))
        high = calculate_indicators(self.indicator_data(high_raw))
        self.assertEqual((low.inflow_pressure, low.spending_intent, low.competition_pressure),
                         (high.inflow_pressure, high.spending_intent, high.competition_pressure))
        self.assertEqual(low.operational_risk, high.operational_risk)
        self.assertEqual(low.contribution_map["footfall"]["footfall_sources"]["inflow"], "oa21285")
        self.assertEqual(low.contribution_map["footfall"]["normalization"], "percentile")

    def test_oa_spike_is_diagnostic_and_sdot_spike_drives_competition(self):
        self.assertIsNone(adapt_oa_history_to_footfall(self.summary(50, count=3, spike=2)))
        adapted = adapt_oa_history_to_footfall(self.summary(50, count=4, spike=3))
        self.assertNotIn("competition_spike_bonus", adapted["contributions"])
        self.assertEqual(adapted["diagnostics"]["oa_temporal_spike_ratio"], 3)
        hybrid = combine_indicator_footfall_sources(
            adapted, {"summary": {"visitor_spike_ratio": 3}, "source_status": {"status": "ok"}}
        )
        result = calculate_indicators(self.indicator_data(hybrid))
        self.assertEqual(result.contribution_map["footfall"]["competition_bonus"], 16)
        self.assertEqual(result.contribution_map["footfall"]["footfall_sources"], {
            "inflow": "oa21285", "spending": "oa21285", "competition": "sdot"
        })

        different_oa = adapt_oa_history_to_footfall(self.summary(50, count=4, spike=1.01))
        different_result = calculate_indicators(self.indicator_data(combine_indicator_footfall_sources(
            different_oa, {"summary": {"visitor_spike_ratio": 3}, "source_status": {"status": "ok"}}
        )))
        self.assertEqual(result.competition_pressure, different_result.competition_pressure)

    def test_four_snapshot_oa_available_when_enabled_and_failed_falls_back(self):
        summary = self.summary(80, count=4, spike=1.2)
        with patch("oa21285_footfall.OA21285_FOOTFALL_ENABLED", True), patch(
            "oa21285_footfall.summarize_district_percentile_history", return_value=summary
        ):
            oa = load_available_oa_footfall("강남구", "2026-08-22", "저녁")
        self.assertEqual(oa["provider"], "oa21285")
        with patch("oa21285_footfall.OA21285_FOOTFALL_ENABLED", True), patch(
            "oa21285_footfall.summarize_district_percentile_history", return_value={"source_status": "failed"}
        ):
            self.assertIsNone(load_available_oa_footfall("강남구", "2026-08-22", "저녁"))

    def test_unavailable_uses_sdot_for_all_indicators(self):
        combined = combine_indicator_footfall_sources(None, {
            "summary": {"visitor_avg_in_band": 10, "visitor_max_in_band": 20, "visitor_spike_ratio": 2},
            "source_status": {"status": "ok"},
        })
        self.assertEqual(combined["footfall_sources"], {
            "inflow": "sdot", "spending": "sdot", "competition": "sdot"
        })
        self.assertTrue(combined["source_status"]["fallback"])
        self.assertEqual(combined["source_status"]["fallback_reason"], "oa_current_date_time_band_unavailable")

    def test_available_oa_still_fetches_sdot_exactly_once_per_request(self):
        oa = adapt_oa_history_to_footfall(self.summary(80))
        loader = Mock(return_value={"summary": {"visitor_spike_ratio": 2}, "source_status": {"status": "ok"}})
        result = resolve_indicator_footfall_sources(oa, loader)
        loader.assert_called_once_with()
        self.assertEqual(result["footfall_sources"], {
            "inflow": "oa21285", "spending": "oa21285", "competition": "sdot"
        })

    def test_normalization_and_llm_keep_oa_semantics_without_visitor_alias(self):
        adapted = adapt_oa_history_to_footfall(self.summary(80))
        normalized = _normalize_footfall(adapted)
        self.assertEqual(normalized["footfall_sources"]["inflow"], "oa21285")
        self.assertNotIn("visitor_avg_in_band", normalized["summary"])
        indicators = calculate_indicators(self.indicator_data(normalized))
        context = build_llm_context({}, {"footfall": normalized}, indicators)
        self.assertIn("동일 시점 서울 자치구 내 상대 위치", context["footfall"]["data_semantics"])


if __name__ == "__main__":
    unittest.main()
