import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import common
from festival_api import FestivalAPI
from oa21285_api import OA21285Client


ROOT = Path(__file__).resolve().parents[1]


class SecretAuditTests(unittest.TestCase):
    def test_common_defaults_are_environment_lookups(self):
        tree = ast.parse((ROOT / "common.py").read_text(encoding="utf-8"))
        expected = {
            "DEFAULT_PUBLIC_DATA_KEY": "DATA_GO_KR_SERVICE_KEY",
            "DEFAULT_SEOUL_KEY": "SEOUL_OPEN_DATA_API_KEY",
            "DEFAULT_KOPIS_KEY": "KOPIS_API_KEY",
        }
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in expected:
                call = node.value
                self.assertIsInstance(call, ast.Call)
                self.assertEqual(ast.unparse(call.func), "os.getenv")
                found[node.targets[0].id] = call.args[0].value
        self.assertEqual(found, expected)

    def test_dotenv_is_ignored_and_example_values_are_empty(self):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", rules)
        example = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        self.assertTrue(example)
        self.assertTrue(all(line.endswith("=") for line in example if line.strip()))

    def test_transport_errors_do_not_expose_key(self):
        fake_key = "unit-test-sensitive-value"
        leak = requests.ConnectionError(f"failed URL containing {fake_key}")
        with patch("common.requests.get", side_effect=leak):
            with self.assertRaises(RuntimeError) as caught:
                common.safe_request_json(f"https://example.invalid/{fake_key}")
        self.assertNotIn(fake_key, str(caught.exception))

        session = Mock()
        session.get.side_effect = leak
        result = OA21285Client(fake_key, session=session).get_place("POI001")
        self.assertNotIn(fake_key, str(result))

        with patch("festival_api.requests.get", side_effect=leak):
            with self.assertRaises(RuntimeError) as caught:
                FestivalAPI(fake_key)._call("searchFestival2")
        self.assertNotIn(fake_key, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
