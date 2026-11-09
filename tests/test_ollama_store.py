"""Legacy and official v2 inventories work offline, including manifest lists."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import fixture

from llm_service_manager import discovery, preflight
from llm_service_manager.ollama_store import (
    complete_ollama,
    manifest_path,
    ollama_manifest,
    sources,
)


class OllamaStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "blobs").mkdir()
        self.layer = self.blob(b"weight fixture")
        self.manifest = {
            "config": self.layer,
            "layers": [
                dict(self.layer, mediaType="application/vnd.ollama.image.model")
            ],
            "runner": "llamacpp",
        }

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest()
        p = self.root / "blobs" / ("sha256-" + digest)
        p.write_bytes(data)
        return {"digest": "sha256:" + digest, "size": len(data)}

    def tag(self, directory, host, manifest, link=False):
        p = self.root / directory / host / "library/qwen3.8/latest"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(manifest).encode()
        if link:
            ref = self.blob(data)
            p.symlink_to(self.root / "blobs" / ref["digest"].replace(":", "-"))
        else:
            p.write_bytes(data)
        return p

    def test_v2_symlink_is_discovered_and_preflight_accepts_without_server(self):
        self.tag("manifests-v2", "ollama.com", self.manifest, link=True)
        self.assertTrue(complete_ollama(self.root, "qwen3.8"))
        self.assertEqual(sources(self.root), ["qwen3.8:latest"])
        c = fixture()
        with (
            patch.object(preflight, "ollama_roots", return_value=[self.root]),
            patch.object(preflight, "available_backends", return_value=["ollama"]),
            patch.object(preflight, "program", return_value="/bin/true"),
        ):
            self.assertEqual(preflight.preflight(c, "ollama")[0], [])
        c["catalog"] = []
        c["model_selection"] = {}
        with (
            patch.object(discovery, "ollama_roots", return_value=[self.root]),
            patch.object(discovery, "available_backends", return_value=["ollama"]),
        ):
            found = discovery.discover(c)["catalog"]
        self.assertEqual([x["source"] for x in found], ["qwen3.8:latest"])

    def test_v2_precedence_and_duplicate_hosts(self):
        self.tag("manifests", "registry.ollama.ai", self.manifest)
        changed = dict(self.manifest, runner="ggml")
        self.tag("manifests-v2", "ollama.com", changed)
        self.assertEqual(ollama_manifest(self.root, "qwen3.8")["runner"], "ggml")
        self.assertEqual(sources(self.root), ["qwen3.8:latest"])
        self.assertEqual(
            ollama_manifest(self.root, "ollama.com/library/qwen3.8"), changed
        )

    def test_registry_port_round_trip(self):
        p = self.tag("manifests-v2", "localhost%3A5000", self.manifest)
        name = "localhost:5000/library/qwen3.8:latest"
        self.assertEqual(manifest_path(self.root, name), p)
        self.assertEqual(sources(self.root), [name])

    def test_manifest_list_references_without_size_and_missing_preferred_runner(self):
        reference = self.blob(json.dumps(self.manifest).encode())
        index = {
            "manifests": [
                dict(digest="sha256:" + "f" * 64, runner="llamacpp"),
                dict(digest=reference["digest"], runner="ggml"),
            ]
        }
        self.tag("manifests-v2", "ollama.com", index, link=True)
        self.assertEqual(ollama_manifest(self.root, "qwen3.8"), self.manifest)

    def test_missing_or_truncated_weight_is_not_reported_installed(self):
        self.tag("manifests-v2", "ollama.com", self.manifest, link=True)
        p = self.root / "blobs" / self.layer["digest"].replace(":", "-")
        p.write_bytes(b"x")
        self.assertFalse(complete_ollama(self.root, "qwen3.8"))
        p.unlink()
        self.assertFalse(complete_ollama(self.root, "qwen3.8"))

    def test_external_manifest_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "manifest"
            outside.write_text(json.dumps(self.manifest))
            p = self.tag("manifests-v2", "ollama.com", self.manifest)
            p.unlink()
            p.symlink_to(outside)
            self.assertFalse(complete_ollama(self.root, "qwen3.8"))
