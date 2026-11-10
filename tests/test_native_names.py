"""Native naming migration is transactional, including legacy service recovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import FakeOps, fixture

from llm_service_manager import manager, native_names, paths, services
from llm_service_manager.config import Error


class NativeNamesTests(unittest.TestCase):
    def test_new_installation_has_no_retired_files(self):
        with patch.object(native_names, "LEGACY_FILES", {}):
            self.assertEqual(native_names.retired_files({}), {})

    def test_migration_rejects_collision_symlink_and_unowned_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old.service", Path(tmp) / "new.service"
            mapping = {str(old): str(new)}
            with patch.object(native_names, "LEGACY_FILES", mapping):
                old.write_text("# Managed by llmsm\nold")
                self.assertEqual(
                    native_names.retired_files({str(new): "new"}), {str(old): None}
                )
                new.write_text("existing")
                with self.assertRaisesRegex(Error, "同时存在"):
                    native_names.retired_files({str(new): "new"})
                new.unlink()
                old.write_text("user owned")
                with self.assertRaisesRegex(Error, "无法确认"):
                    native_names.retired_files({str(new): "new"})
                old.unlink()
                old.symlink_to(new)
                with self.assertRaisesRegex(Error, "符号链接"):
                    native_names.retired_files({str(new): "new"})

    def test_migration_commit_and_rollback_preserve_files_and_old_active_unit(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                old, new = root / "old.service", root / "new.service"
                old.write_text("# Managed by llmsm\nold contents")
                config = root / "config.json"
                config.write_text(json.dumps(fixture()))
                before = config.read_bytes()
                ops = FakeOps(root / "state", fail=fail)
                ops.running = ["llm-llama.service"]
                with (
                    patch.object(native_names, "LEGACY_FILES", {str(old): str(new)}),
                    patch.object(
                        manager, "available_backends", return_value=["llama.cpp"]
                    ),
                    patch.object(manager, "preflight", return_value=([], [])),
                    patch.object(
                        manager, "native", return_value={str(new): "new contents"}
                    ),
                ):
                    if fail:
                        with self.assertRaises(Error):
                            manager.switch(
                                fixture(),
                                "llama.cpp",
                                manager.revision(config),
                                ops,
                                config,
                            )
                        self.assertEqual(
                            old.read_text(), "# Managed by llmsm\nold contents"
                        )
                        self.assertFalse(new.exists())
                        self.assertEqual(config.read_bytes(), before)
                        self.assertEqual(ops.running, ["llm-llama.service"])
                    else:
                        manager.switch(
                            fixture(),
                            "llama.cpp",
                            manager.revision(config),
                            ops,
                            config,
                        )
                        self.assertFalse(old.exists())
                        self.assertEqual(new.read_text(), "new contents")
                        self.assertEqual(ops.running, ["llama-server-local.service"])
                self.assertFalse((ops.state / "pending.json").exists())

    def test_legacy_service_can_be_reported_before_migration(self):
        with patch.object(
            paths.Path, "exists", lambda p: p.name == "llm-llama.service"
        ):
            self.assertEqual(paths.existing_unit("llama.cpp"), "llm-llama.service")

    def test_rollback_uses_original_unit_and_original_ollama_modelfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp)
            with (
                patch.object(services, "read_config", return_value=fixture()),
                patch.object(
                    services.Path,
                    "exists",
                    lambda p: str(p) == "/etc/ollama/llmsm.Modelfile",
                ),
                patch.object(ops, "ready") as ready,
                patch.object(
                    services.Path,
                    "read_text",
                    return_value="ExecStart=llama serve --model old.gguf",
                ),
                patch.object(ops, "wait_ready") as wait_ready,
                patch.object(ops, "check_gpu_offload") as gpu,
            ):
                ops.restored({"active": ["ollama.service", "llm-llama.service"]})
                self.assertEqual(
                    ready.call_args_list[0].kwargs["modelfile"],
                    "/etc/ollama/llmsm.Modelfile",
                )
                self.assertEqual(ready.call_count, 1)
                wait_ready.assert_called_once_with(
                    fixture(), "llama.cpp", "llm-llama.service"
                )
                gpu.assert_called_once_with("llm-llama.service")
