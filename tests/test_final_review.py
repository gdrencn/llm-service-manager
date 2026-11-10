"""Regression coverage for issues found during the final whole-project review."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import FakeOps, fixture
from test_inline import interact

from llm_service_manager import config, manager, services
from llm_service_manager.paths import UNITS


class FinalReviewTests(unittest.TestCase):
    def test_corrupt_recovery_metadata_never_stops_services(self):
        bad_fields = [
            {"enabled": []},
            {"enabled": {"foreign.service": "enabled"}},
            {"enabled": {UNITS["ollama"]: None}},
            {"config_path": None},
            {"config_path": "relative.json"},
        ]
        for fields in bad_fields:
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as tmp:
                ops = FakeOps(Path(tmp))
                txn = {"files": {}, "active": [], **fields}
                pending = Path(tmp) / "pending.json"
                pending.write_text(json.dumps(txn))
                with patch.object(ops, "stop") as stop:
                    with self.assertRaises(config.Error):
                        manager.recover(ops)
                stop.assert_not_called()
                self.assertTrue(pending.exists())

    def test_recovery_rejects_all_symlinks_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("keep")
            link = root / "link"
            link.symlink_to(target)
            ops = FakeOps(root)
            (root / "pending.json").write_text(
                json.dumps({"active": [], "files": {str(link): None}})
            )
            with patch.object(ops, "stop") as stop:
                with self.assertRaises(config.Error):
                    manager.recover(ops)
            stop.assert_not_called()
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "keep")

    def test_runtime_autostart_rollback_removes_persistent_enablement(self):
        unit = UNITS["ollama"]
        ops = services.Services()
        with (
            patch.object(ops, "available_units", return_value=[unit]),
            patch.object(ops, "run") as run,
        ):
            ops.restore_boot({"enabled": {unit: "enabled-runtime"}})
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["systemctl", "disable", unit],
                ["systemctl", "enable", "--runtime", unit],
            ],
        )

    def test_unsupported_models_field_is_a_configuration_error(self):
        for model in ({"path": None}, {"source": []}, {"mtp_supported": "false"}):
            with self.subTest(model=model), self.assertRaises(config.Error):
                config.normalized({"models": {"llama.cpp": model}})
        c = fixture()
        c["catalog"][0]["mtp_supported"] = "false"
        with self.assertRaises(config.Error):
            config.validate(c)

    def test_small_plain_menu_does_not_confirm_invisible_choice(self):
        script = """import os
from inline_ui import UI,Cancelled
os.environ['LINES']='3'
with UI(translate=lambda zh,en:en) as ui:
 try:ui.choose('Title',[('danger','Must not execute')],details=['Details'])
 except Cancelled:pass
 else:raise AssertionError('invisible choice accepted')
print('SAFE')
"""
        rc, out = interact([sys.executable, "-c", script], [(b"Enlarge", b"\r\x1b")])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"SAFE", out)

    def test_framework_imports_are_independent_of_application(self):
        import ast

        root = Path(__file__).resolve().parents[1] / "inline_ui"
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name.startswith("llm_service_manager") for name in imports),
                    path,
                )

    def test_install_inventory_does_not_trust_old_mtp_capability_flag(self):
        from llm_service_manager.inventory import llama_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
            vision = root / "mmproj.gguf"
            for p in (main, vision):
                p.write_bytes(b"GGUF" + b"\0" * 28)
            c = fixture()
            c["catalog"][1].update(
                path=str(main), vision_path=str(vision), mtp_supported=True
            )
            self.assertFalse(llama_model(c, root))

    def test_systemd_runtime_enablement_recovery_in_isolated_root(self):
        import shutil
        import subprocess

        if not shutil.which("systemctl"):
            self.skipTest("systemctl is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = UNITS["ollama"]
            units = root / "etc/systemd/system"
            units.mkdir(parents=True)
            (units / unit).write_text(
                "[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=multi-user.target\n"
            )
            (units / "multi-user.target").write_text(
                "[Unit]\nDescription=Test target\n"
            )

            def run(args, *a, **kw):
                return subprocess.run(
                    [args[0], "--root", tmp, *args[1:]],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            run(["systemctl", "enable", unit])
            self.assertTrue((units / "multi-user.target.wants" / unit).is_symlink())
            ops = services.Services()
            with (
                patch.object(ops, "available_units", return_value=[unit]),
                patch.object(ops, "run", side_effect=run),
            ):
                ops.restore_boot({"enabled": {unit: "enabled-runtime"}})
            self.assertFalse((units / "multi-user.target.wants" / unit).is_symlink())
            self.assertTrue(
                (
                    root / "run/systemd/system/multi-user.target.wants" / unit
                ).is_symlink()
            )
