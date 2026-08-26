import unittest
from unittest.mock import Mock, patch
import os

import requests

from common import TOURISM_DATA_SEOUL_SIGNGU
from tourism_baseline_api import (
    TOURISM_INDICATOR_CODES,
    TourismBaselineClient,
    _resolve_service_key,
    get_stay_intensity,
    get_tourism_baseline_raw,
)


def response_payload(item, code="0000", message="OK"):
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": message},
            "body": {
                "items": {"item": item},
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 1 if item else 0,
            },
        }
    }


def client_for(payload=None, http_error=None):
    response = Mock()
    if http_error:
        response.status_code = 503
        response.raise_for_status.side_effect = http_error
    else:
        response.raise_for_status.return_value = None
        response.json.return_value = payload
    session = Mock()
    session.get.return_value = response
    return TourismBaselineClient("test-key", session=session), session


class TourismBaselineApiTests(unittest.TestCase):
    def test_does_not_fallback_to_generic_public_data_key_env(self):
        with patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "generic-key"}, clear=True), patch(
            "tourism_baseline_api._read_project_dotenv_value", return_value=""
        ):
            with self.assertRaises(ValueError):
                _resolve_service_key(None)

    def test_all_25_seoul_district_codes_exist(self):
        self.assertEqual(len(TOURISM_DATA_SEOUL_SIGNGU), 25)
        self.assertEqual(TOURISM_DATA_SEOUL_SIGNGU["강남구"], "11680")
        self.assertEqual(TOURISM_DATA_SEOUL_SIGNGU["강동구"], "11740")
        self.assertEqual(len(set(TOURISM_DATA_SEOUL_SIGNGU.values())), 25)

    def test_normal_json_response(self):
        item = {"baseYm": "202601", "signguCd": "11680", "tarSjrnDsIxVal": "72.3"}
        client, _ = client_for(response_payload(item))
        result = get_stay_intensity("강남구", "202601", client=client)
        self.assertEqual(result["source_status"]["status"], "ok")
        self.assertEqual(result["items"], [item])
        self.assertEqual(result["total_count"], 1)

    def test_items_single_dict(self):
        item = {"touDivIxCd": "3101", "touDivIxVal": "54.1"}
        client, _ = client_for(response_payload(item))
        result = client.request(
            "areaTouDivList", base_ym="202601", area_cd="11", signgu_cd="11680", indicator_code="3101"
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [item])

    def test_items_list(self):
        items = [{"expDivIxCd": "3201"}, {"expDivIxCd": "3202"}]
        client, _ = client_for(response_payload(items))
        result = client.request(
            "areaExpDivList", base_ym="202601", area_cd="11", signgu_cd="11680", indicator_code="3201"
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"], items)

    def test_normal_no_data_body(self):
        client, _ = client_for(response_payload([]))
        result = get_stay_intensity("강남구", "202601", client=client)
        self.assertEqual(result["source_status"]["status"], "no_data")
        self.assertEqual(result["items"], [])

    def test_nodata_result_code(self):
        client, _ = client_for(response_payload([], code="03", message="NODATA_ERROR"))
        result = get_stay_intensity("강남구", "202601", client=client)
        self.assertEqual(result["source_status"]["status"], "no_data")
        self.assertEqual(result["result_code"], "03")

    def test_api_result_code_error(self):
        client, _ = client_for(response_payload([], code="10", message="INVALID_REQUEST_PARAMETER_ERROR"))
        result = get_stay_intensity("강남구", "202601", client=client)
        self.assertEqual(result["source_status"]["status"], "failed")
        self.assertEqual(result["result_code"], "10")

    def test_http_error(self):
        client, _ = client_for(http_error=requests.HTTPError("503 Server Error"))
        result = get_stay_intensity("강남구", "202601", client=client)
        self.assertEqual(result["source_status"]["status"], "failed")
        self.assertIn("503", result["source_status"]["reason"])
        self.assertNotIn("test-key", str(result))
        self.assertNotIn("serviceKey", str(result))

    def test_invalid_district(self):
        client, _ = client_for(response_payload([]))
        with self.assertRaises(ValueError):
            get_stay_intensity("부산진구", "202601", client=client)

    def test_operation_indicator_code_parameter(self):
        client, session = client_for(response_payload({"value": "1"}))
        operations = {
            "areaTarSjrnDsList": ("tarSjrnDsIxCd", "2101"),
            "areaTarExpDsList": ("tarExpDsIxCd", "2201"),
            "areaTouDivList": ("touDivIxCd", "3101"),
            "areaExpDivList": ("expDivIxCd", "3201"),
            "areaIntlDivList": ("intlDivIxCd", "3301"),
            "areaTarSvcDemList": ("tarSvcDemIxCd", "1101"),
            "areaCulResDemList": ("culResDemIxCd", "1201"),
        }
        for operation, (parameter, code) in operations.items():
            result = client.request(
                operation, base_ym="202601", area_cd="11", signgu_cd="11680", indicator_code=code
            )
            params = session.get.call_args.kwargs["params"]
            self.assertEqual(params[parameter], code)
            self.assertNotIn("serviceKey", result["request_params"])

    def test_supported_indicator_code_lists(self):
        self.assertEqual(TOURISM_INDICATOR_CODES["stay_intensity"], ["2101", "2102", "2103", "2104", "2105"])
        self.assertEqual(TOURISM_INDICATOR_CODES["spending_intensity"], ["2201", "2202", "2203"])
        self.assertEqual(len(TOURISM_INDICATOR_CODES["tourist_diversity"]), 7)
        self.assertEqual(len(TOURISM_INDICATOR_CODES["spending_diversity"]), 7)
        self.assertEqual(TOURISM_INDICATOR_CODES["international_diversity"], ["3301", "3302", "3303"])
        self.assertEqual(len(TOURISM_INDICATOR_CODES["service_demand"]), 12)
        self.assertEqual(len(TOURISM_INDICATOR_CODES["cultural_resource_demand"]), 5)
        for invalid_prefix in ("11", "12", "21", "22", "31", "32", "33"):
            self.assertNotIn(invalid_prefix, sum(TOURISM_INDICATOR_CODES.values(), []))

    def test_indicator_code_is_omitted_by_default(self):
        client, session = client_for(response_payload({"value": "1"}))
        get_stay_intensity("강남구", "202601", client=client)
        params = session.get.call_args.kwargs["params"]
        self.assertNotIn("tarSjrnDsIxCd", params)

    def test_repeated_indicator_codes_with_partial_no_data(self):
        mock_client = Mock()
        def request(operation, **kwargs):
            code = kwargs.get("indicator_code")
            status = "no_data" if code == "2102" else "ok"
            return {
                "source_status": {"status": status},
                "count": 0 if status == "no_data" else 1,
                "items": [] if status == "no_data" else [{"code": code}],
            }
        mock_client.request.side_effect = request
        with patch("tourism_baseline_api.TourismBaselineClient", return_value=mock_client):
            result = get_tourism_baseline_raw(
                "강남구", "202601", "test-key",
                indicator_codes={"stay_intensity": ["2101", "2102"]},
            )
        block = result["stay_intensity"]
        self.assertEqual(block["source_status"]["status"], "ok")
        self.assertEqual(block["source_status"]["indicator_codes"], {"2101": "ok", "2102": "no_data"})
        self.assertEqual(len(block["requests"]), 2)

    def test_repeated_operation_is_failed_if_any_code_fails(self):
        mock_client = Mock()
        def request(operation, **kwargs):
            status = "failed" if operation == "areaTarSjrnDsList" else "no_data"
            return {"source_status": {"status": status}, "count": 0, "items": []}
        mock_client.request.side_effect = request
        with patch("tourism_baseline_api.TourismBaselineClient", return_value=mock_client):
            result = get_tourism_baseline_raw(
                "강남구", "202601", "test-key",
                indicator_codes={"stay_intensity": ["2101", "2102"]},
            )
        self.assertEqual(result["stay_intensity"]["source_status"]["status"], "failed")
        self.assertEqual(result["source_status"]["status"], "failed")

    def test_aggregate_shape_and_status(self):
        client, session = client_for(response_payload({"value": "1"}))
        with patch("tourism_baseline_api.TourismBaselineClient", return_value=client):
            result = get_tourism_baseline_raw("강남구", "2026-01", "test-key")
        self.assertEqual(result["source_status"]["status"], "ok")
        self.assertEqual(result["signgu_cd"], "11680")
        for key in (
            "stay_intensity", "spending_intensity", "tourist_diversity", "spending_diversity",
            "international_diversity", "service_demand", "cultural_resource_demand",
        ):
            self.assertIn(key, result)
        expected_request_count = sum(len(codes) for codes in TOURISM_INDICATOR_CODES.values())
        self.assertEqual(session.get.call_count, expected_request_count)
        requested_codes = {
            value
            for call in session.get.call_args_list
            for key, value in call.kwargs["params"].items()
            if key.endswith("IxCd")
        }
        self.assertEqual(requested_codes, set(sum(TOURISM_INDICATOR_CODES.values(), [])))


if __name__ == "__main__":
    unittest.main()
