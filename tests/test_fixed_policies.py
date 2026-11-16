import copy
import json
import unittest

from test_config import STRATA, fixture

from llm_service_manager.adapters import native
from llm_service_manager.config import Error
from llm_service_manager.paths import STRATA_CONFIG
from llm_service_manager.policies import fixed_environment
from llm_service_manager.tui import setting_rows


class FixedPolicyTests(unittest.TestCase):
    def test_strata_overrides_routing_but_preserves_other_native_environment(self):
        for env in (
            None,
            {},
            {
                "STRATA_ROUTE_TAIL_SKIP": "7",
                "STRATA_ROUTE_PRIOR": "1.5",
                "OTHER": "keep",
            },
        ):
            profile = copy.deepcopy(STRATA)
            profile["env"] = env
            rendered = native(
                fixture(), lambda _: json.dumps(profile), backends=["strata"]
            )
            actual = json.loads(rendered[STRATA_CONFIG])["env"]
            self.assertEqual(actual, {**(env or {}), **fixed_environment("strata")})
            self.assertEqual(profile["env"], env)

    def test_malformed_environment_is_rejected(self):
        for env in ([], "bad", False):
            with self.assertRaises(Error):
                native(
                    fixture(),
                    lambda _: json.dumps(dict(STRATA, env=env)),
                    backends=["strata"],
                )

    def test_only_strata_shows_fixed_noneditable_rows(self):
        for backend in ("ollama", "llama.cpp", "strata"):
            rows, disabled = setting_rows(fixture(), backend)
            self.assertEqual([key for key, _ in rows[-2:]], ["review", "back"])
            fixed = [
                (key, str(text))
                for key, text in rows
                if key.startswith("fixed:STRATA_")
            ]
            self.assertEqual(len(fixed), 2 if backend == "strata" else 0)
            for key, text in fixed:
                self.assertIn(key, disabled)
                self.assertIn("0", text)
                self.assertIn("[不可修改]", text)
                self.assertNotIn("不适用", text)

    def test_fixed_values_are_not_shared_mutable_state(self):
        values = fixed_environment("strata")
        values["STRATA_ROUTE_PRIOR"] = "1.5"
        self.assertEqual(fixed_environment("strata")["STRATA_ROUTE_PRIOR"], "0")

    def test_authentication_policy_ui_and_units(self):
        for backend in ("ollama", "llama.cpp", "strata"):
            rows, disabled = setting_rows(fixture(), backend)
            rows = dict(rows)
            if backend == "ollama":
                self.assertNotIn("fixed:API_KEY", rows)
                continue
            self.assertIn("fixed:API_KEY", disabled)
            self.assertIn("关闭", str(rows["fixed:API_KEY"]))
            self.assertIn("[不可修改]", str(rows["fixed:API_KEY"]))
            files = native(fixture(), lambda _: json.dumps(STRATA), backends=[backend])
            unit = next(
                text for path, text in files.items() if path.endswith(".service")
            )
            expected = (
                "STRATA_API_KEY"
                if backend == "strata"
                else "LLAMA_API_KEY LLAMA_ARG_API_KEY_FILE"
            )
            self.assertIn("UnsetEnvironment=" + expected + "\n", unit)
            self.assertNotIn('"--api-key"', unit)
            self.assertNotIn('"--api-key-file"', unit)
