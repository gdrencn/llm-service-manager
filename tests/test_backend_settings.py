"""Independent persisted backend settings with a shared endpoint and boot policy."""

import copy
import json
import os
import pwd
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import STRATA, FakeOps, fixture

from llm_service_manager import manager, tui
from llm_service_manager.adapters import native
from llm_service_manager.config import SCHEMA_VERSION, Error, value
from llm_service_manager.config_document import decode, encode, for_backend
from llm_service_manager.paths import LLAMA_PRESET, OLLAMA_MODELFILE, STRATA_CONFIG
from llm_service_manager.storage import read_config, revision


def config():
    document = encode(fixture())
    for backend, context in (("ollama", 4096), ("llama.cpp", 8192), ("strata", 262144)):
        document[backend]["settings"]["context"]["value"] = context
    document["target"] = {"backend": "ollama", "model": "o"}
    return decode(document)


class BackendSettingsTests(unittest.TestCase):
    def test_first_use_initialization_writes_a_readable_scoped_document(self):
        # Execute only the embedded Python configuration writer, never the shell
        # installer, package commands, downloads, or service operations.
        root = Path(__file__).resolve().parent.parent
        template = (root / "scripts/install.sh.in").read_text()
        source = template.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            source = source.replace(
                "Path('/etc/llm-service-manager/config.json')", f"Path({str(path)!r})"
            )
            with (
                patch("sys.argv", ["-", str(root), pwd.getpwuid(os.getuid()).pw_name]),
                patch("sys.path", list(sys.path)),
                patch("llm_service_manager.network.free_port", return_value=55556),
                patch("subprocess.run", side_effect=AssertionError("no commands")),
            ):
                exec(compile(source, "installer-config-init", "exec"), {})
                c = read_config(path)
                self.assertEqual(c["schema"], SCHEMA_VERSION)
                self.assertEqual(value(c, "port"), 55556)
                for backend in ("ollama", "llama.cpp", "strata"):
                    self.assertEqual(value(for_backend(c, backend), "context"), 131072)
                self.assertEqual(json.loads(path.read_text()), encode(c))
                before = path.read_bytes()
                exec(compile(source, "installer-config-recheck", "exec"), {})
                self.assertEqual(path.read_bytes(), before)

    def test_backend_validation_keeps_editor_open_and_preserves_changes(self):
        c = for_backend(config(), "llama.cpp")
        ui = Mock()
        ui.choose.side_effect = [
            "context",
            "flash_attention",
            False,
            "review",
            "flash_attention",
            True,
            "review",
        ]
        ui.input.return_value = "16k"
        edited = tui.edit(ui, c, "llama.cpp")
        ui.text.assert_called_once()
        self.assertIn("Flash Attention", ui.text.call_args.args[1])
        self.assertEqual(value(edited, "context"), 16384)
        self.assertEqual(value(c, "context"), 8192)
        self.assertEqual(encode(edited)["ollama"], encode(c)["ollama"])

    def test_failed_switch_restores_all_scopes_and_only_target_native_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            c = config()
            path.write_text(json.dumps(encode(c)))
            before = path.read_bytes()
            candidate = for_backend(c, "llama.cpp")
            candidate["settings"]["context"]["value"] = 16384
            candidate["settings"]["port"]["value"] = 55556
            candidate["settings"]["autostart"]["value"] = False
            target = root / "llama.ini"
            other = root / "ollama.conf"
            target.write_text("original llama")
            target.chmod(0o640)
            other.write_text("original ollama")
            ops = FakeOps(root / "state", fail=True)
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(
                    manager, "native", return_value={str(target): "candidate"}
                ),
                self.assertRaises(Error),
            ):
                manager.switch(candidate, "llama.cpp", revision(path), ops, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(target.read_text(), "original llama")
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(other.read_text(), "original ollama")
            self.assertEqual(ops.running, ["ollama.service"])
            self.assertFalse((ops.state / "pending.json").exists())
            self.assertEqual(value(read_config(path), "context"), 4096)
            self.assertEqual(
                value(for_backend(read_config(path), "llama.cpp"), "context"), 8192
            )

    def test_roundtrip_and_switching_views_do_not_mix_values(self):
        c = config()
        original = copy.deepcopy(c)
        llama = for_backend(c, "llama.cpp")
        self.assertEqual(value(llama, "context"), 8192)
        llama["settings"]["context"]["value"] = 16384
        llama["settings"]["mtp"]["value"] = False
        result = encode(llama)
        self.assertEqual(result["ollama"], encode(c)["ollama"])
        self.assertEqual(result["strata"], encode(c)["strata"])
        self.assertEqual(
            value(for_backend(decode(result), "llama.cpp"), "context"), 16384
        )
        self.assertEqual(value(for_backend(llama, "ollama"), "context"), 4096)
        self.assertEqual(c, original)
        self.assertEqual(encode(decode(result)), result)

    def test_global_settings_are_shared_and_runtime_lives_in_global(self):
        c = for_backend(config(), "strata")
        c["settings"]["port"]["value"] = 55556
        c["settings"]["autostart"]["value"] = False
        c["runtime"]["home"] = "/srv/models"
        document = encode(c)
        self.assertNotIn("settings", document)
        self.assertNotIn("runtime", document)
        self.assertEqual(document["global"]["runtime"]["home"], "/srv/models")
        for backend in ("ollama", "llama.cpp", "strata"):
            view = for_backend(decode(document), backend)
            self.assertEqual(value(view, "port"), 55556)
            self.assertFalse(value(view, "autostart"))
            self.assertNotIn("port", document[backend]["settings"])

    def test_native_renderers_receive_each_backends_own_values(self):
        files = native(config(), lambda _: json.dumps(STRATA))
        self.assertIn("num_ctx 4096\n", files[OLLAMA_MODELFILE])
        self.assertIn("ctx-size = 8192\n", files[LLAMA_PRESET])
        args = json.loads(files[STRATA_CONFIG])["args"]
        self.assertEqual(args[args.index("--max-context") + 1], "262144")

    def test_disk_read_selects_current_backend(self):
        document = encode(config())
        document["target"] = {"backend": "strata", "model": "s"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(document))
            self.assertEqual(value(read_config(path), "context"), 262144)

    def test_missing_sections_use_defaults_without_copying_other_backend(self):
        c = decode({"ollama": {"settings": {"context": {"value": 4096}}}})
        self.assertEqual(value(c, "context"), 4096)
        self.assertEqual(value(for_backend(c, "llama.cpp"), "context"), 131072)

    def test_misplaced_parameters_and_old_disk_format_are_rejected(self):
        for raw in (
            {"schema": 2},
            {"settings": {}},
            {"global": {"settings": {"context": {"value": 4096}}}},
            {"ollama": {"settings": {"port": {"value": 55555}}}},
            {"strata": {"settings": {"fit": {"value": True}}}},
        ):
            with self.subTest(raw=raw), self.assertRaises(Error):
                decode(raw)

    def test_apply_generates_only_target_and_persists_only_its_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            c = config()
            before = encode(c)
            path.write_text(json.dumps(before))
            c = for_backend(c, "ollama")
            c["settings"]["context"]["value"] = 16384
            target = root / "ollama.conf"
            other = root / "llama.ini"
            other.write_text("do not touch")
            ops = FakeOps(root / "state")
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(
                    manager, "native", return_value={str(target): "selected"}
                ) as render,
            ):
                manager.switch(c, "ollama", revision(path), ops, path)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(render.call_args.kwargs, {"backends": ["ollama"]})
            self.assertEqual(value(render.call_args.args[0], "context"), 16384)
            after = json.loads(path.read_text())
            self.assertEqual(after["llama.cpp"], before["llama.cpp"])
            self.assertEqual(after["strata"], before["strata"])
            self.assertEqual(other.read_text(), "do not touch")
            self.assertEqual(value(read_config(path), "context"), 16384)
