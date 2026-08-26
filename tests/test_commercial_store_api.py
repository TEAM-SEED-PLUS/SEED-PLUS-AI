import unittest
from unittest.mock import Mock

import requests

from commercial_store_api import CommercialStoreAPIError, CommercialStoreClient, dedupe_stores


def response(payload, status=200):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.side_effect = None if status == 200 else requests.HTTPError("failed")
    return value


def payload(code="00", items=None, total=0):
    return {"response": {"header": {"resultCode": code, "resultMsg": "message"}, "body": {
        "totalCount": total, "pageNo": 1, "numOfRows": 1000,
        "items": {"item": items or []},
    }}}


class CommercialStoreClientTests(unittest.TestCase):
    def client(self, *responses):
        session = Mock()
        session.get.side_effect = responses
        return CommercialStoreClient("secret-key", session=session, min_interval_seconds=0), session

    def test_normal_response_and_key_is_not_returned(self):
        client, session = self.client(response(payload(items=[{"bizesId": "A", "lat": 37.5}], total=1)))
        result = client.stores_in_dong("signguCd", "11680")
        self.assertEqual(result["items"][0]["bizesId"], "A")
        self.assertNotIn("serviceKey", str(result))
        self.assertEqual(session.get.call_args.kwargs["params"]["serviceKey"], "secret-key")

    def test_no_data(self):
        client, _ = self.client(response(payload(code="03")))
        self.assertEqual(client.stores_in_dong("signguCd", "00000")["status"], "no_data")

    def test_api_error_does_not_include_key(self):
        client, _ = self.client(response(payload(code="99")))
        with self.assertRaises(CommercialStoreAPIError) as caught:
            client.stores_in_dong("signguCd", "11680")
        self.assertNotIn("secret-key", str(caught.exception))

    def test_pagination_and_duplicate_id_removal(self):
        client, _ = self.client(
            response(payload(items=[{"bizesId": "A"}, {"bizesId": "B"}], total=1001)),
            response(payload(items=[{"bizesId": "B"}, {"bizesId": "C"}], total=1001)),
        )
        result = client.fetch_all_in_dong("signguCd", "11680")
        self.assertEqual(result["pages_requested"], 2)
        self.assertEqual([item["bizesId"] for item in result["items"]], ["A", "B", "C"])
        self.assertEqual(result["duplicate_count"], 1)

    def test_null_coordinate_and_category_are_preserved(self):
        row = {"bizesId": "A", "lat": None, "lon": None, "indsSclsCd": None}
        self.assertEqual(dedupe_stores([row]), [row])

    def test_rows_are_capped_at_live_limit(self):
        client, session = self.client(response(payload()))
        client.stores_in_dong("signguCd", "11680", rows=9999)
        self.assertEqual(session.get.call_args.kwargs["params"]["numOfRows"], 1000)


if __name__ == "__main__":
    unittest.main()
