"""Real filesystem renames exercise shared inventory checks; no model is loaded."""

import json
import tempfile
import unittest
from pathlib import Path

from test_config import fixture

from llm_service_manager.artifacts import complete_asset, strata_assets
from llm_service_manager.availability import model_present
from llm_service_manager.preflight import preflight


class MissingFileTests(unittest.TestCase):
    def test_strata_core_and_optional_files_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            draft = root / "draft"
            pack.mkdir()
            draft.mkdir()
            tokenizer = pack / "tokenizer"
            tokenizer.mkdir()
            cfg = {
                "cwd": tmp,
                "exe": "component:strata-engine",
                "tokenizer": str(tokenizer),
                "args": [
                    "--pack",
                    str(pack),
                    "--native",
                    str(root / "dense.gguf"),
                    "--ple-gguf",
                    str(root / "ple.gguf"),
                    "--mtp",
                    str(draft),
                ],
                "vision": {
                    "mmproj": str(root / "mmproj.gguf"),
                    "exe": "/missing/vision",
                },
            }
            # Explicit filenames avoid deriving fixture completeness from the code under test.
            names = [
                "dense.gguf",
                "ple.gguf",
                "mmproj.gguf",
                "pack/dense.bin",
                "pack/experts.bin",
                "pack/index.txt",
                "pack/tokenizer/vocab.json",
                "pack/tokenizer/merges.txt",
                "pack/tokenizer/token_type.json",
                "draft/dense.bin",
                "draft/dense.txt",
                "draft/experts.bin",
                "draft/draft_vocab.bin",
            ]
            for name in names:
                p = root / name
                p.write_bytes(b"GGUF" + b"\0" * 28 if p.suffix == ".gguf" else b"data")
            path = root / "strata.json"
            path.write_text(json.dumps(cfg))
            c = fixture()
            m = c["catalog"][2]
            m["config_path"] = str(path)
            self.assertTrue(model_present(c, m))
            self.assertEqual(set(strata_assets(cfg)), {root / n for n in names})
            for name in names:
                with self.subTest(name=name):
                    p = root / name
                    hidden = p.with_suffix(p.suffix + ".hidden")
                    p.rename(hidden)
                    try:
                        optional = name.startswith("draft/") or name == "mmproj.gguf"
                        self.assertEqual(model_present(c, m), optional)
                        self.assertFalse(
                            all(complete_asset(a) for a in strata_assets(cfg))
                        )
                        errors, _ = preflight(c, "strata")
                        self.assertTrue(any(str(p) in e for e in errors), errors)
                        c["settings"]["vision"]["value"] = False
                        c["settings"]["mtp"]["value"] = False
                        errors, _ = preflight(c, "strata")
                        self.assertEqual(any(str(p) in e for e in errors), not optional)
                    finally:
                        hidden.rename(p)
                        c["settings"]["vision"]["value"] = True
                        c["settings"]["mtp"]["value"] = True
            self.assertTrue(model_present(c, m))

    def test_missing_gguf_shard_is_not_selectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "model-00001-of-00002.gguf"
            second = Path(tmp) / "model-00002-of-00002.gguf"
            for p in (first, second):
                p.write_bytes(b"GGUF" + b"\0" * 28)
            m = {"backend": "llama.cpp", "path": str(first)}
            self.assertTrue(model_present({}, m))
            hidden = second.with_suffix(".hidden")
            second.rename(hidden)
            try:
                self.assertFalse(model_present({}, m))
            finally:
                hidden.rename(second)
            self.assertTrue(model_present({}, m))

    def test_strata_tokenizer_path_is_required(self):
        for value in (None, "", "relative/tokenizer", []):
            with self.subTest(value=value):
                cfg = {
                    "args": ["--pack", "/pack", "--native", "/dense.gguf"],
                    "tokenizer": value,
                }
                with self.assertRaisesRegex(ValueError, "tokenizer"):
                    strata_assets(cfg, vision=False, mtp=False)
