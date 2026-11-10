"""Model selection must not change user settings or gate backend capabilities."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import fixture

from llm_service_manager import preflight
from llm_service_manager.adapters import native
from llm_service_manager.paths import LLAMA_PRESET, OLLAMA_MODELFILE


class ModelSettingsPassthroughTests(unittest.TestCase):
    def test_llama_unknown_model_keeps_mtp_and_vision_without_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.gguf"
            path.write_bytes(b"GGUF" + bytes(32))
            c = fixture()
            m = c["catalog"][1]
            m.update(path=str(path), vision_path="", mtp_path="")
            m.pop("mtp_supported")
            before = copy.deepcopy(c)
            with (
                patch.object(
                    preflight, "available_backends", return_value=["llama.cpp"]
                ),
                patch.object(preflight, "program", return_value="/bin/true"),
            ):
                self.assertEqual(preflight.preflight(c, "llama.cpp")[0], [])
            preset = native(c, backends=["llama.cpp"])[LLAMA_PRESET]
            self.assertIn("spec-type = draft-mtp\n", preset)
            self.assertIn("spec-draft-n-max = 4\n", preset)
            self.assertIn("mmproj-auto = true\n", preset)
            self.assertIn("mmproj-offload = true\n", preset)
            self.assertNotIn("mmproj = ", preset)
            self.assertNotIn("no-mmproj", preset)
            self.assertEqual(c, before)

    def test_ollama_does_not_gate_mtp_on_recorded_capability(self):
        c = fixture()
        c["catalog"][0]["mtp_supported"] = False
        before = copy.deepcopy(c)
        with (
            patch.object(preflight, "available_backends", return_value=["ollama"]),
            patch.object(preflight, "program", return_value="/bin/true"),
            patch.object(preflight, "ollama_roots", return_value=[Path("/models")]),
            patch.object(preflight, "manifest_path", return_value=Path(__file__)),
            patch.object(preflight, "ollama_manifest", return_value={}),
        ):
            self.assertEqual(preflight.preflight(c, "ollama")[0], [])
        modelfile = native(c, backends=["ollama"])[OLLAMA_MODELFILE]
        self.assertIn("PARAMETER draft_num_predict 4\n", modelfile)
        self.assertEqual(c, before)

    def test_explicit_missing_sidecar_is_still_a_file_error(self):
        c = fixture()
        c["catalog"][1]["vision_path"] = "/nonexistent/explicit-mmproj.gguf"
        with (
            patch.object(preflight, "available_backends", return_value=["llama.cpp"]),
            patch.object(preflight, "program", return_value="/bin/true"),
        ):
            errors, _ = preflight.preflight(c, "llama.cpp")
        self.assertTrue(any("/nonexistent/explicit-mmproj.gguf" in e for e in errors))
