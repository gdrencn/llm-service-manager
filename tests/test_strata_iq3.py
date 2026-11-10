"""Official IQ3_S recipe and GGUF-native layout; no installer or download runs."""

import json
import shlex
import tempfile
import unittest
from pathlib import Path

from llm_service_manager.artifacts import complete_asset
from llm_service_manager.commands import explanation, recipe_specs
from llm_service_manager.inventory import strata_model


class StrataIQ3Tests(unittest.TestCase):
    def test_complete_recipes_keep_vision_mtp_and_no_start_explicit(self):
        recipes = {r.key: r for r in recipe_specs("strata")}
        for key, family, model in (
            ("strata.model", "nvfp4", "NVFP4"),
            ("strata.iq3_s", "qwen", "IQ3_S"),
        ):
            args = shlex.split(recipes[key].command)
            for flag, expected in (
                ("--family", family),
                ("--model", model),
                ("--vision", "gpu"),
                ("--mtp", "on"),
                ("--drafter", "mtp"),
            ):
                self.assertEqual(args[args.index(flag) + 1], expected)
            for flag in ("--no-start", "--no-browser", "--yes"):
                self.assertIn(flag, args)
        self.assertIn("IQ3_S", explanation("strata", recipes["strata.iq3_s"].command))

    def test_native_iq_pack_and_second_shard_ple_are_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            names = [
                "model-00001-of-00002.gguf",
                "model-00002-of-00002.gguf",
                "vision.gguf",
                "pack/dense.bin",
                "pack/index.txt",
                "pack/native_experts.txt",
                "pack/tokenizer/vocab.json",
                "pack/tokenizer/merges.txt",
                "pack/tokenizer/token_type.json",
                "mtp/dense.bin",
                "mtp/dense.txt",
                "mtp/experts.bin",
                "mtp/draft_vocab.bin",
            ]
            for name in names:
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    b"GGUF" + bytes(28) if path.suffix == ".gguf" else b"data"
                )
            cfg = {
                "args": [
                    "--pack",
                    str(home / "pack"),
                    "--native",
                    str(home / names[0]),
                    "--ple-gguf",
                    str(home / names[1]),
                    "--mtp",
                    str(home / "mtp"),
                ],
                "tokenizer": str(home / "pack/tokenizer"),
                "vision": {"mmproj": str(home / "vision.gguf")},
            }
            profile = home / ".local/share/strata/install/strata-iq3_s.json"
            profile.parent.mkdir(parents=True)
            profile.write_text(json.dumps(cfg))
            self.assertTrue(strata_model(home, "strata-iq3_s.json"))
            self.assertFalse(strata_model(home))
            self.assertTrue(complete_asset(home / names[1]))
            for name in names:
                with self.subTest(missing=name):
                    path = home / name
                    hidden = path.with_name(path.name + ".hidden")
                    path.rename(hidden)
                    try:
                        self.assertFalse(strata_model(home, "strata-iq3_s.json"))
                    finally:
                        hidden.rename(path)
