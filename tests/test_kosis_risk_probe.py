import unittest
from unittest.mock import Mock, patch

import requests

from kosis_risk_probe import KosisAPIError, KosisClient, dedupe_rows, normalize_row, parse_year
from kosis_risk_calibration import calibration_rows, distribution


def response(payload, error=None):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.side_effect = error
    return value


def row(**overrides):
    value = {"ORG_ID":"101","TBL_ID":"DT_X","TBL_NM":"표","PRD_DE":"2023",
             "ITM_ID":"T3","ITM_NM":"소멸률","UNIT_ID":"%","UNIT_NM":"%","DT":"10.5",
             "C1":"11","C1_NM":"서울","C2":"I","C2_NM":"숙박·음식점업"}
    value.update(overrides)
    return value


class KosisRiskProbeTests(unittest.TestCase):
    def client(self, *responses, retries=0):
        session = Mock()
        session.get.side_effect = responses
        return KosisClient("secret-key", session=session, retries=retries, retry_delay=0), session

    def call(self, client):
        return client.statistics(org_id="101", table_id="DT_X", item_id="T3",
                                 object_ids={"objL1":"ALL"}, start_year=2023, end_year=2023)

    def test_normal_response_parses_region_and_industry(self):
        client, session = self.client(response([row()]))
        result = self.call(client)
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual(result["items"][0]["region"]["name"], "서울")
        self.assertEqual(result["items"][0]["industry"]["code"], "I")
        self.assertEqual(session.get.call_args.kwargs["params"]["apiKey"], "secret-key")
        self.assertNotIn("secret-key", str(result))

    def test_no_data(self):
        client, _ = self.client(response([]))
        self.assertEqual(self.call(client)["source_status"], "no_data")

    def test_failed_response_does_not_leak_key(self):
        client, _ = self.client(response({"err":"30", "message":"secret-key"}))
        with self.assertRaises(KosisAPIError) as caught:
            self.call(client)
        self.assertNotIn("secret-key", str(caught.exception))

    def test_malformed_response(self):
        client, _ = self.client(response("not-json-object"))
        with self.assertRaises(KosisAPIError):
            self.call(client)

    def test_transport_retry_is_bounded_and_sanitized(self):
        client, session = self.client(response({}, requests.Timeout("secret-key")),
                                      response([row()]), retries=1)
        self.assertEqual(self.call(client)["source_status"], "ok")
        self.assertEqual(session.get.call_count, 2)

    def test_same_grain_dedupe(self):
        rows, duplicates = dedupe_rows([row(), row(DT="11.0"), row(C2="G")])
        self.assertEqual((len(rows), duplicates), (2, 1))

    def test_year_parsing(self):
        self.assertEqual(parse_year("2023Q1"), 2023)
        self.assertIsNone(parse_year("bad"))

    def test_normalizer_rejects_malformed_row(self):
        with self.assertRaises(KosisAPIError):
            normalize_row([])

    def test_calibration_distribution_and_direction(self):
        rows = calibration_rows([{"rate": 3.0}, {"rate": 9.0}, {"rate": 15.0}], "rate")
        self.assertLess(rows[0]["percentile_midrank"], rows[2]["percentile_midrank"])
        self.assertEqual(rows[0]["min_max_0_10"], 0.0)
        self.assertEqual(rows[2]["min_max_0_10"], 10.0)
        self.assertEqual(distribution([3, 9, 15])["median"], 9)


if __name__ == "__main__":
    unittest.main()
