import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from performance_cache import get_cached_performances
from performance_collector import collect_performance_once
from performance_api import KOPISClient


def row(identifier, title):
    return {"mt20id": identifier, "prfnm": title, "fcltynm": f"{title} 극장",
            "prfpdfrom": "2026.09.01", "prfpdto": "2026.09.30",
            "genrenm": "연극", "openrun": "N", "poster": "poster"}


class FakeClient:
    def __init__(self, rows, *, detail_fail=(), facility_fail=()):
        self.rows = rows
        self.detail_fail = set(detail_fail)
        self.facility_fail = set(facility_fail)
        self.detail_calls = []
        self.facility_calls = []

    def get_all_performances(self, *_args, **_kwargs):
        return self.rows

    def get_performance_detail(self, identifier):
        self.detail_calls.append(identifier)
        if identifier in self.detail_fail:
            raise RuntimeError("detail down")
        return {"mt20id": identifier, "mt10id": f"F-{identifier}", "dtguidance": "10:00, 20:00",
                "prfruntime": "90분", "pcseguidance": "10,000원", "genrenm": "연극"}

    def get_facility_detail(self, identifier):
        self.facility_calls.append(identifier)
        if identifier in self.facility_fail:
            raise RuntimeError("facility down")
        district = "종로구" if identifier.endswith("1") else "강남구"
        return {"mt10id": identifier, "fcltynm": "극장", "adres": f"서울특별시 {district} 테스트로 1"}


class PerformanceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, client):
        return collect_performance_once("2026-09-12", client=client, data_dir=self.root)

    def test_seoul_wide_three_rows_create_nonempty_multidistrict_snapshot(self):
        client = FakeClient([row("PF1", "종로 공연"), row("PF2", "강남 공연"), row("PF3", "강남 공연2")])
        result = self.collect(client)
        self.assertEqual(result["item_count"], 3)
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual({x["district_from_address"] for x in result["items"]}, {"종로구", "강남구"})
        self.assertEqual(result["diagnostics"]["raw_list_count"], 3)
        self.assertEqual(result["diagnostics"]["unique_mt20id_count"], 3)

    def test_detail_failure_preserves_list_item_and_is_not_empty(self):
        result = self.collect(FakeClient([row("PF1", "정상"), row("PF2", "상세 실패")], detail_fail={"PF2"}))
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["source_status"], "partial")
        self.assertEqual(result["diagnostics"]["detail_failure_count"], 1)

    def test_facility_failure_preserves_item(self):
        result = self.collect(FakeClient([row("PF1", "정상"), row("PF2", "시설 실패")], facility_fail={"F-PF2"}))
        failed = next(x for x in result["items"] if x["mt20id"] == "PF2")
        self.assertEqual(failed["address"], "")
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["source_status"], "partial")
        loaded = get_cached_performances("종로구", "2026-09-12", "10:38", data_dir=self.root)
        self.assertEqual(loaded["source_status"]["status"], "partial")

    def test_empty_list_is_empty(self):
        result = self.collect(FakeClient([]))
        self.assertEqual((result["item_count"], result["source_status"]), (0, "empty"))

    def test_http_200_kopis_error_record_is_not_empty(self):
        root = ET.fromstring("<dbs><db><returncode>02</returncode><errmsg>bad key</errmsg></db></dbs>")
        client = KOPISClient("not-a-secret")
        with patch.object(client, "_get", return_value=root):
            with self.assertRaisesRegex(RuntimeError, "code=02"):
                client.get_performances_page("20260912", "20260912", signgucode="11")

    def test_all_enrichment_failure_is_failed_not_empty(self):
        result = self.collect(FakeClient([row("PF1", "A"), row("PF2", "B")], detail_fail={"PF1", "PF2"}))
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["source_status"], "failed")

    @patch("performance_api.KOPISClient", side_effect=AssertionError("network forbidden"))
    def test_loader_district_and_morning_evening_parity_without_network(self, network):
        self.collect(FakeClient([row("PF1", "종로 공연"), row("PF2", "강남 공연")]))
        morning = get_cached_performances("종로구", "2026-09-12", "10:38", data_dir=self.root)
        evening = get_cached_performances("종로구", "2026-09-12", "20:30", data_dir=self.root)
        gangnam = get_cached_performances("강남구", "2026-09-12", "10:38", data_dir=self.root)
        self.assertEqual([x["title"] for x in morning["items"]], ["종로 공연"])
        self.assertEqual([x["title"] for x in evening["items"]], ["종로 공연"])
        self.assertEqual([x["title"] for x in gangnam["items"]], ["강남 공연"])
        self.assertEqual(morning["items"][0]["time_text"], "10:00")
        self.assertEqual(evening["items"][0]["time_text"], "20:00")
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
