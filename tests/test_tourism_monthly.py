import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from common import TOURISM_DATA_SEOUL_SIGNGU
from tourism_baseline_api import (
    INDICATOR_FIELD_SPECS,
    OPERATION_SPECS_BY_METRIC,
    TOURISM_INDICATOR_CODES,
    TourismBaselineClient,
)
from tourism_monthly import (
    EXPECTED_INDICATOR_COUNT,
    apply_cached_fallbacks,
    collect_tourism_month,
    extract_tourism_indicator_rows,
    get_district_tourism_baseline,
    get_cached_district_tourism_baseline,
    load_cached_tourism_snapshot,
    load_tourism_snapshot,
    normalize_tourism_month,
    normalize_tourism_rows,
    refresh_tourism_month,
)


class FakeMonthlyClient:
    def __init__(self, *, missing=None, failed_codes=None, unknown=False):
        self.calls = []
        self.missing = missing or {}
        self.failed_codes = set(failed_codes or [])
        self.unknown = unknown

    def request_all_pages(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        code = kwargs["indicator_code"]
        metric = next(name for name, op in OPERATION_SPECS_BY_METRIC.items() if op == operation)
        if code in self.failed_codes:
            return {
                "source_status": {"status": "failed", "reason": "mock failure"},
                "items": [], "count": 0, "api_call_count": 1, "page_count": 1, "total_count": 0,
            }
        code_field, name_field, value_field = INDICATOR_FIELD_SPECS[metric]
        missing_codes = set(self.missing.get(code, []))
        items = []
        for index, (district, signgu_cd) in enumerate(TOURISM_DATA_SEOUL_SIGNGU.items()):
            if signgu_cd in missing_codes:
                continue
            items.append({
                "baseYm": kwargs["base_ym"], "areaCd": "11", "areaNm": "서울특별시",
                "signguCd": signgu_cd, "signguNm": district,
                code_field: code, name_field: f"지표 {code}", value_field: index,
            })
        items.append({
            "baseYm": kwargs["base_ym"], "signguCd": "0", "signguNm": "_",
            code_field: code, name_field: f"지표 {code}", value_field: 999,
        })
        if self.unknown:
            items.append({
                "baseYm": kwargs["base_ym"], "signguCd": "99999", "signguNm": "알수없음",
                code_field: code, name_field: f"지표 {code}", value_field: 1,
            })
        return {
            "source_status": {"status": "ok"}, "items": items, "count": len(items),
            "api_call_count": 1, "page_count": 1, "total_count": len(items),
        }


class TourismMonthlyCollectionTests(unittest.TestCase):
    def test_collects_42_indicators_in_42_seoul_wide_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeMonthlyClient()
            raw = collect_tourism_month("202509", data_dir=directory, client=client)
        self.assertEqual(EXPECTED_INDICATOR_COUNT, 42)
        self.assertEqual(raw["api_call_count"], 42)
        self.assertEqual(len(client.calls), 42)
        self.assertEqual(len(raw["rows"]), 42 * 25)
        self.assertEqual(raw["diagnostics"]["excluded_aggregate_rows"], 42)
        self.assertTrue(all(call[1]["signgu_cd"] is None for call in client.calls))
        self.assertTrue(all(call[1]["num_of_rows"] == 1000 for call in client.calls))
        self.assertTrue(all(status["district_count"] == 25 for status in raw["indicator_status"].values()))

    def test_aggregate_excluded_and_unknown_code_diagnosed(self):
        client = FakeMonthlyClient(unknown=True)
        result = client.request_all_pages(
            "areaTarSjrnDsList", base_ym="202509", area_cd="11", signgu_cd=None,
            indicator_code="2101", num_of_rows=1000,
        )
        rows, diagnostics = extract_tourism_indicator_rows("stay_intensity", "2101", "202509", result)
        self.assertEqual(len(rows), 25)
        self.assertEqual(diagnostics["excluded_aggregate_rows"], 1)
        self.assertEqual(diagnostics["unknown_district_codes"], ["99999"])

    def test_missing_district_becomes_no_data_and_zero_remains_ok(self):
        missing_code = TOURISM_DATA_SEOUL_SIGNGU["송파구"]
        client = FakeMonthlyClient(missing={"2101": [missing_code]})
        result = client.request_all_pages(
            "areaTarSjrnDsList", base_ym="202509", area_cd="11", signgu_cd=None,
            indicator_code="2101", num_of_rows=1000,
        )
        rows, _ = extract_tourism_indicator_rows("stay_intensity", "2101", "202509", result)
        songpa = next(row for row in rows if row["district"] == "송파구")
        first = next(row for row in rows if row["raw_value"] == 0)
        self.assertEqual(songpa["source_status"], "no_data")
        self.assertIsNone(songpa["raw_value"])
        self.assertEqual(first["source_status"], "ok")


class TourismMonthlyNormalizationTests(unittest.TestCase):
    @staticmethod
    def row(district, code, value, status="ok", month="202510"):
        return {
            "district": district, "signgu_cd": TOURISM_DATA_SEOUL_SIGNGU[district],
            "metric_group": "stay_intensity", "indicator_code": code,
            "indicator_name": code, "raw_value": value, "requested_month": month,
            "source_month": month, "source_status": status,
        }

    def test_independent_min_max_and_equal_values(self):
        rows = [
            self.row("강남구", "2101", 0), self.row("송파구", "2101", 5), self.row("성동구", "2101", 10),
            self.row("강남구", "2102", 7), self.row("송파구", "2102", 7), self.row("성동구", "2102", 7),
        ]
        normalized, diagnostics = normalize_tourism_rows(rows)
        values = {(row["indicator_code"], row["district"]): row["normalized_value"] for row in normalized}
        self.assertEqual(values[("2101", "강남구")], 0.0)
        self.assertEqual(values[("2101", "송파구")], 50.0)
        self.assertEqual(values[("2101", "성동구")], 100.0)
        self.assertTrue(all(values[("2102", district)] == 50.0 for district in ("강남구", "송파구", "성동구")))
        self.assertEqual(diagnostics["2102"]["normalization_reason"], "all_values_equal")

    def test_missing_status_has_no_normalized_value(self):
        rows = [self.row("강남구", "2101", None, "no_data"), self.row("송파구", "2101", None, "failed")]
        normalized, _ = normalize_tourism_rows(rows)
        self.assertTrue(all(row["normalized_value"] is None for row in normalized))

    def test_fallback_value_participates_and_preserves_source_month(self):
        rows = [
            self.row("강남구", "2101", 0),
            {**self.row("송파구", "2101", 5, "fallback"), "source_month": "202509", "fallback_reason": "current_month_no_data"},
            self.row("성동구", "2101", 10),
        ]
        normalized, _ = normalize_tourism_rows(rows)
        songpa = next(row for row in normalized if row["district"] == "송파구")
        self.assertEqual(songpa["normalized_value"], 50.0)
        self.assertEqual(songpa["source_month"], "202509")
        self.assertEqual(songpa["source_status"], "fallback")


class TourismMonthlyFallbackStorageTests(unittest.TestCase):
    def _write_previous(self, directory, status="ok"):
        path = Path(directory) / "raw" / "202509.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"rows": [{
            "district": "송파구", "signgu_cd": "11710", "metric_group": "stay_intensity",
            "indicator_code": "2101", "indicator_name": "타권역 방문자 비중", "raw_value": 70.4,
            "requested_month": "202509", "source_month": "202509", "source_status": status,
        }]}, ensure_ascii=False), encoding="utf-8")

    def test_no_data_and_failed_use_last_cached_success(self):
        for current_status, expected_reason in (
            ("no_data", "current_month_no_data"), ("failed", "current_month_api_failed")
        ):
            with self.subTest(current_status=current_status), tempfile.TemporaryDirectory() as directory:
                self._write_previous(directory)
                current = [{
                    "district": "송파구", "signgu_cd": "11710", "metric_group": "stay_intensity",
                    "indicator_code": "2101", "indicator_name": "", "raw_value": None,
                    "requested_month": "202510", "source_month": "202510", "source_status": current_status,
                }]
                row = apply_cached_fallbacks(current, "202510", data_dir=directory)[0]
                self.assertEqual(row["raw_value"], 70.4)
                self.assertEqual(row["source_month"], "202509")
                self.assertEqual(row["source_status"], "fallback")
                self.assertEqual(row["fallback_reason"], expected_reason)

    def test_raw_normalized_manifest_save_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeMonthlyClient()
            snapshot = refresh_tourism_month("202509", data_dir=directory, client=client)
            self.assertEqual(snapshot["snapshot_status"], "complete")
            self.assertTrue((Path(directory) / "raw" / "202509.json").exists())
            self.assertTrue((Path(directory) / "normalized" / "202509.json").exists())
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            entry = manifest["snapshots"]["202509"]
            self.assertEqual(entry["api_call_count"], 42)
            self.assertEqual(entry["excluded_aggregate_rows"], 42)
            self.assertEqual(entry["success_value_count"], 1050)
            district = get_district_tourism_baseline("강남구", "202509", data_dir=directory)
            self.assertEqual(district["signgu_cd"], "11680")

    def test_complete_cache_skips_api_and_refresh_calls_api(self):
        with tempfile.TemporaryDirectory() as directory:
            first = FakeMonthlyClient()
            refresh_tourism_month("202509", data_dir=directory, client=first)
            cached_client = Mock()
            load_tourism_snapshot("202509", data_dir=directory, client=cached_client)
            cached_client.request_all_pages.assert_not_called()
            refreshed = FakeMonthlyClient()
            load_tourism_snapshot("202509", data_dir=directory, client=refreshed, refresh=True)
            self.assertEqual(len(refreshed.calls), 42)

    def test_api_key_is_never_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            refresh_tourism_month("202509", service_key="super-secret-key", data_dir=directory, client=FakeMonthlyClient())
            saved = "".join(path.read_text(encoding="utf-8") for path in Path(directory).rglob("*.json"))
            self.assertNotIn("super-secret-key", saved)
            self.assertNotIn("serviceKey", saved)

    def test_missing_requested_snapshot_preserves_stale_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            normalized = Path(directory) / "normalized"
            normalized.mkdir(parents=True)
            for month, value in (("202508", 10), ("202509", 70)):
                payload = {
                    "requested_month": month,
                    "snapshot_status": "complete",
                    "districts": {"강남구": {"signgu_cd": "11680", "metrics": {
                        "stay_intensity": {"2101": {
                            "normalized_value": value, "raw_value": value,
                            "requested_month": month, "source_month": month, "source_status": "ok",
                        }}
                    }}},
                }
                (normalized / f"{month}.json").write_text(json.dumps(payload), encoding="utf-8")

            snapshot = load_cached_tourism_snapshot("202612", data_dir=directory)
            baseline = get_cached_district_tourism_baseline("강남구", "202612", data_dir=directory)
            self.assertEqual(snapshot["source_month"], "202509")
            self.assertEqual(snapshot["age_months"], 15)
            self.assertEqual(baseline["requested_month"], "202612")
            self.assertEqual(baseline["source_month"], "202509")
            self.assertEqual(baseline["source_status"], "fallback")
            self.assertEqual(baseline["fallback_reason"], "fallback_age_exceeded")
            component = baseline["metrics"]["stay_intensity"]["2101"]
            self.assertEqual(component["source_status"], "fallback")
            self.assertEqual(component["age_months"], 15)

    def test_raw_fallback_uses_one_two_three_months_and_excludes_four(self):
        for source_month, requested_month, expected_age, usable in (
            ("202611", "202612", 1, True),
            ("202610", "202612", 2, True),
            ("202609", "202612", 3, True),
            ("202608", "202612", 4, False),
        ):
            with self.subTest(source_month=source_month), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "raw" / f"{source_month}.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"rows": [{
                    "district": "송파구", "signgu_cd": "11710", "metric_group": "stay_intensity",
                    "indicator_code": "2101", "raw_value": 70,
                    "requested_month": source_month, "source_month": source_month, "source_status": "ok",
                }]}), encoding="utf-8")
                current = [{
                    "district": "송파구", "signgu_cd": "11710", "metric_group": "stay_intensity",
                    "indicator_code": "2101", "raw_value": None, "requested_month": requested_month,
                    "source_month": requested_month, "source_status": "no_data",
                }]
                row = apply_cached_fallbacks(current, requested_month, data_dir=directory)[0]
                self.assertEqual(row["source_month"], source_month)
                self.assertEqual(row["age_months"], expected_age)
                self.assertEqual(row["source_status"], "fallback" if usable else "no_data")
                self.assertEqual(row["raw_value"], 70 if usable else None)

    def test_failed_snapshot_is_skipped_but_partial_snapshot_is_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            normalized = Path(directory) / "normalized"
            normalized.mkdir(parents=True)
            for month, status in (("202508", "partial"), ("202509", "failed")):
                payload = {
                    "requested_month": month,
                    "snapshot_status": status,
                    "districts": {"강남구": {"signgu_cd": "11680", "metrics": {}}},
                }
                (normalized / f"{month}.json").write_text(json.dumps(payload), encoding="utf-8")
            snapshot = load_cached_tourism_snapshot("202510", data_dir=directory)
            self.assertEqual(snapshot["source_month"], "202508")
            self.assertEqual(snapshot["snapshot_status"], "partial")
            self.assertEqual(snapshot["source_status"], "fallback")


class TourismPaginationTests(unittest.TestCase):
    @staticmethod
    def response(items, total, page):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "response": {
                "header": {"resultCode": "0000", "resultMsg": "OK"},
                "body": {"items": {"item": items}, "pageNo": page, "numOfRows": 2, "totalCount": total},
            }
        }
        return response

    def test_paginates_and_deduplicates_rows(self):
        one = {"signguCd": "11110", "tarSjrnDsIxCd": "2101"}
        two = {"signguCd": "11140", "tarSjrnDsIxCd": "2101"}
        three = {"signguCd": "11170", "tarSjrnDsIxCd": "2101"}
        session = Mock()
        session.get.side_effect = [self.response([one, two], 3, 1), self.response([two, three], 3, 2)]
        client = TourismBaselineClient("test-key", session=session)
        result = client.request_all_pages(
            "areaTarSjrnDsList", base_ym="202509", area_cd="11", signgu_cd=None,
            indicator_code="2101", num_of_rows=2,
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["api_call_count"], 2)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["pageNo"], 2)
        self.assertNotIn("signguCd", session.get.call_args_list[0].kwargs["params"])


if __name__ == "__main__":
    unittest.main()
