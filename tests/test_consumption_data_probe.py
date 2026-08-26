import unittest
from unittest.mock import Mock

import requests

from consumption_data_probe import ConsumptionDataError, SeoulConsumptionClient


def response(value, failed=False):
    item = Mock()
    item.json.return_value = value
    item.raise_for_status.side_effect = requests.HTTPError("failed") if failed else None
    return item


def sales(rows, total=None, code="INFO-000"):
    return {"VwsmSignguSelngW": {"list_total_count": len(rows) if total is None else total,
        "RESULT": {"CODE": code}, "row": rows}}


class ConsumptionProbeTests(unittest.TestCase):
    def client(self, *responses):
        session = Mock(); session.get.side_effect = responses
        return SeoulConsumptionClient("secret-key", session=session), session

    def test_estimated_sales_normal_and_key_not_returned(self):
        client, session = self.client(response(sales([{"STDR_YYQU_CD": "20251"}])))
        result = client.estimated_sales_page()
        self.assertEqual(result["source_status"], "ok")
        self.assertNotIn("secret-key", str(result))
        self.assertIn("secret-key", session.get.call_args.args[0])

    def test_pagination_and_duplicate_grain(self):
        row = {"STDR_YYQU_CD": "20251", "SIGNGU_CD": "1", "SVC_INDUTY_CD": "A"}
        client, _ = self.client(response(sales([row], total=2)), response(sales([row], total=2)))
        result = client.estimated_sales_all(page_size=1)
        self.assertEqual(result["api_call_count"], 2)
        self.assertEqual(result["duplicate_grain_count"], 1)

    def test_no_data(self):
        client, _ = self.client(response(sales([], code="INFO-200")))
        self.assertEqual(client.estimated_sales_page()["source_status"], "no_data")

    def test_failed_and_malformed(self):
        client, _ = self.client(response({}, failed=True))
        with self.assertRaises(ConsumptionDataError) as caught:
            client.estimated_sales_page()
        self.assertNotIn("secret-key", str(caught.exception))
        client, _ = self.client(response(sales([], code="ERROR-500")))
        with self.assertRaises(ConsumptionDataError):
            client.estimated_sales_page()
        client, _ = self.client(response([]))
        with self.assertRaises(ConsumptionDataError): client.estimated_sales_page()

    def test_realtime_normal_no_data_and_malformed(self):
        client, _ = self.client(response({"RESULT": {"resultCode": "INFO-000"}, "AREA_CD": "P1",
            "AREA_NM": "place", "LIVE_CMRCL_STTS": {"AREA_CMRCL_LVL": "보통"}}))
        self.assertEqual(client.realtime_commerce("P1")["commercial"]["AREA_CMRCL_LVL"], "보통")
        client, _ = self.client(response({"RESULT": {"resultCode": "INFO-200"}}))
        self.assertEqual(client.realtime_commerce("P1")["source_status"], "no_data")
        client, _ = self.client(response({"RESULT": {"resultCode": "INFO-000"}}))
        with self.assertRaises(ConsumptionDataError): client.realtime_commerce("P1")

    def test_oa22173_page(self):
        payload = {"VwsmSignguStorW": {"list_total_count": 1, "RESULT": {"CODE": "INFO-000"},
                   "row": [{"STDR_YYQU_CD": "20261", "SIMILR_INDUTY_STOR_CO": "3"}]}}
        client, _ = self.client(response(payload))
        result = client.district_stores_page()
        self.assertEqual(result["items"][0]["SIMILR_INDUTY_STOR_CO"], "3")


if __name__ == "__main__": unittest.main()
