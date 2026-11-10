"""Naming preserves publisher labels while internal IDs remain stable."""

import json
import tempfile
import unittest
from unittest.mock import patch

from test_config import STRATA, fixture

from llm_service_manager import config, discovery, services
from llm_service_manager.adapters import native
from llm_service_manager.model_names import public_name


class ModelNamesTests(unittest.TestCase):
    def test_gguf_keeps_variant_but_not_extension_or_shard_number(self):
        for name in [
            "Qwen3.8-27B-UD-Q4_K_XL.gguf",
            "Qwen3.8-27B-UD-Q4_K_XL-00001-of-00003.gguf",
        ]:
            entry = {
                "id": "initial-llamacpp",
                "backend": "llama.cpp",
                "path": "/models/" + name,
            }
            self.assertEqual(public_name(entry), "Qwen3.8-27B-UD-Q4_K_XL")

    def test_ollama_keeps_namespace_tag_and_registry_port(self):
        for source, expected in [
            ("qwen3.8:latest", "qwen3.8:latest"),
            ("vendor/model:27b-q4_K_M", "vendor/model:27b-q4_K_M"),
            ("localhost:5000/vendor/model", "localhost:5000/vendor/model:latest"),
        ]:
            self.assertEqual(
                public_name({"backend": "ollama", "source": source}), expected
            )

    def test_read_migrates_names_not_selection_or_ids(self):
        c = fixture()
        c["catalog"][0]["served"] = "qwen3.8-local"
        migrated = config.normalized(c)
        self.assertEqual(migrated["model_selection"], c["model_selection"])
        self.assertEqual([m["id"] for m in migrated["catalog"]], ["o", "l", "s"])
        self.assertEqual(migrated["catalog"][0]["name"], "qwen3.8:latest")
        self.assertNotIn("served", migrated["catalog"][0])
        self.assertEqual(migrated["catalog"][1]["name"], "model")

    def test_llama_alias_does_not_leak_internal_id(self):
        c = fixture()
        c["catalog"][1]["id"] = "initial-llamacpp"
        c["model_selection"]["llama.cpp"] = "initial-llamacpp"
        result = native(c, lambda _: json.dumps(STRATA))
        self.assertIn(
            "[model]\n",
            result["/etc/llama.cpp/llama-local.ini"],
        )
        self.assertNotIn(
            "initial-llamacpp", result["/etc/systemd/system/llama-server-local.service"]
        )

    def test_strata_preserves_official_name_verbatim(self):
        source = dict(STRATA, model_name="qwen3.8-flash-next-nvfp4-nvfp4")
        result = native(fixture(), lambda _: json.dumps(source))
        self.assertEqual(
            json.loads(result["/etc/strata/strata-local.json"])["model_name"],
            source["model_name"],
        )

    def test_ollama_create_uses_downloaded_tag_even_with_old_alias(self):
        c = fixture()
        c["catalog"][0]["served"] = "qwen3.8-local"
        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp)
            with (
                patch.object(services, "program", return_value="/bin/ollama"),
                patch.object(ops, "run") as run,
            ):
                ops.create_ollama_model(c)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("create") + 1], "qwen3.8:latest")

    def test_discovery_migrates_already_known_entries(self):
        c = fixture()
        with patch.object(discovery, "available_backends", return_value=[]):
            result = discovery.discover(c)
        self.assertEqual(result["catalog"][1]["name"], "model")
