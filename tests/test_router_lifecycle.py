"""Router-only configuration and native release boundaries."""

import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_config import fixture

from llm_service_manager.adapters import native
from llm_service_manager.config import Error
from llm_service_manager.model_lifecycle import release, wait_router_loaded
from llm_service_manager.paths import LLAMA_PRESET, UNITS


def listing(state, **extra):
    return {"data": [{"id": "model", "status": {"value": state, **extra}}]}


class RouterTests(unittest.TestCase):
    def test_unload_failure_or_reload_cannot_be_reported_successful(self):
        c = fixture()
        c["target"] = {"backend": "llama.cpp"}
        for responses in (
            [listing("loading")],
            [listing("loaded"), {"success": False}],
            [listing("loaded"), {"success": True}, listing("loaded")],
        ):
            ops = Mock()
            ops.active.return_value = [UNITS["llama.cpp"]]
            ops.http.side_effect = responses
            with self.assertRaises(Error):
                release(c, ops, timeout=0)
            ops.stop.assert_not_called()
            ops.event.assert_not_called()

    def test_service_disappearance_is_not_a_successful_release(self):
        c = fixture()
        c["target"] = {"backend": "llama.cpp"}
        ops = Mock()
        ops.active.side_effect = [[UNITS["llama.cpp"]], []]
        ops.http.return_value = listing("unloaded")
        with self.assertRaises(Error):
            release(c, ops)
        ops.event.assert_not_called()

    def test_native_router_only_and_cache_isolated(self):
        files = native(fixture(), backends=["llama.cpp"])
        unit = files["/etc/systemd/system/" + UNITS["llama.cpp"]]
        self.assertIn('"--models-preset"', unit)
        self.assertNotIn('"--model"', unit)
        self.assertIn('"--models-max" "1"', unit)
        self.assertIn("LLAMA_CACHE=/var/cache/llama-server-local", unit)
        preset = files[LLAMA_PRESET]
        for text in [
            "load-on-startup = true",
            "gpu-layers = all",
            "mmproj = /models/mm.gguf",
            "spec-type = draft-mtp",
        ]:
            self.assertIn(text, preset)

    def test_preset_delimiters_are_rejected_before_writing(self):
        for path in ["/models/a#b.gguf", "/models/a;b.gguf"]:
            c = fixture()
            c["catalog"][1]["path"] = path
            with self.assertRaises(Error):
                native(c, backends=["llama.cpp"])

    def test_waits_for_model_not_merely_router_health(self):
        ops = Mock()
        ops.http.side_effect = [listing("loading"), listing("loaded")]
        with patch("llm_service_manager.model_lifecycle.time.sleep"):
            wait_router_loaded(ops, "http://localhost", "model")
        self.assertEqual(ops.http.call_count, 2)
        ops.http.return_value = listing("unloaded", failed=True)
        ops.http.side_effect = None
        with self.assertRaises(Error):
            wait_router_loaded(ops, "http://localhost", "model")

    def test_release_keeps_service_and_checks_unloaded(self):
        c = fixture()
        c["target"] = {"backend": "llama.cpp"}
        ops = Mock()
        ops.active.return_value = [UNITS["llama.cpp"]]
        ops.http.side_effect = [
            listing("loaded"),
            {"success": True},
            listing("unloaded"),
        ]
        release(c, ops)
        ops.stop.assert_not_called()
        self.assertTrue(
            any("/models/unload" in x.args[0] for x in ops.http.call_args_list)
        )
        self.assertEqual(ops.active.call_count, 2)

    def test_already_unloaded_does_not_post_or_wake(self):
        c = fixture()
        c["target"] = {"backend": "llama.cpp"}
        ops = Mock()
        ops.active.return_value = [UNITS["llama.cpp"]]
        ops.http.return_value = listing("unloaded")
        release(c, ops)
        self.assertTrue(all(len(x.args) == 1 for x in ops.http.call_args_list))

    def test_stopped_service_is_not_started(self):
        ops = Mock()
        ops.active.return_value = []
        release(fixture(), ops)
        ops.http.assert_not_called()
        ops.run.assert_not_called()

    def test_wrong_service_is_not_touched(self):
        ops = Mock()
        ops.active.return_value = [UNITS["ollama"]]
        with self.assertRaises(Error):
            release(fixture(), ops)
        ops.http.assert_not_called()

    def test_strata_busy_is_not_force_stopped(self):
        c = fixture()
        c["target"] = {"backend": "strata"}
        ops = Mock()
        ops.active.return_value = [UNITS["strata"]]
        ops.http.side_effect = [
            {"loaded": True},
            HTTPError("url", 409, "busy", {}, None),
        ]
        with self.assertRaisesRegex(Error, "正在处理请求"):
            release(c, ops)
        ops.stop.assert_not_called()

    def test_ollama_unloads_all_resident_models(self):
        c = fixture()
        c["target"] = {"backend": "ollama"}
        ops = Mock()
        ops.active.return_value = [UNITS["ollama"]]
        ops.http.side_effect = [
            {"models": [{"name": "a"}, {"name": "b"}]},
            {},
            {},
            {"models": []},
        ]
        release(c, ops)
        posts = [x.args[1] for x in ops.http.call_args_list if len(x.args) > 1]
        self.assertEqual([x["model"] for x in posts], ["a", "b"])
        self.assertTrue(all(x["keep_alive"] == 0 for x in posts))
