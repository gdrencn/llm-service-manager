"""Configuration, menu validity, service lifecycle and service readiness regressions.
No real installer or download is invoked.
"""

import itertools
import json
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import STRATA, fixture

from llm_service_manager import (
    artifacts,
    availability,
    config,
    discovery,
    manager,
    preflight,
    services,
    storage,
    tui,
)
from llm_service_manager.adapters import native


class HardeningTests(unittest.TestCase):
    def test_uninstalled_backends_are_not_offered(self):
        for combination in itertools.product((False, True), repeat=3):
            installed = [
                backend
                for backend, present in zip(config.BACKENDS, combination)
                if present
            ]
            with self.subTest(installed=installed):
                ui = Mock()
                ui.choose.return_value = "back"
                with patch.object(tui, "available_backends", return_value=installed):
                    tui.selection_flow(ui, fixture(), "r")
                if installed:
                    self.assertEqual(
                        [key for key, _ in ui.choose.call_args.args[1]],
                        installed + ["back"],
                    )
                else:
                    ui.choose.assert_not_called()
                    ui.text.assert_called_once()

    def test_stale_model_hidden_and_cancellation_does_not_stop(self):
        ui = Mock()
        ui.choose.return_value = "ollama"
        with (
            patch.object(tui, "available_backends", return_value=["ollama"]),
            patch.object(tui, "model_present", return_value=False),
            patch.object(tui, "switch") as switch,
        ):
            tui.selection_flow(ui, fixture(), "r")
            switch.assert_not_called()
            self.assertEqual(ui.choose.call_count, 1)

    def test_empty_machine_config_is_valid_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "missing.json"
            c = storage.read_config(p)
            self.assertFalse(p.exists())
            self.assertEqual(c["catalog"], [])
            self.assertEqual(manager.revision(p), "<absent>")
            self.assertEqual(
                c["runtime"]["user"],
                pwd.getpwnam(
                    os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
                ).pw_name,
            )

    def test_malformed_config_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config"
            for bad in (
                "{bad",
                "[]",
                '{"settings": []}',
                '{"settings":{"port":42}}',
                '{"catalog":[null]}',
            ):
                p.write_text(bad)
                with self.assertRaises(config.Error):
                    storage.read_config(p)

    def test_broken_strata_args_do_not_raise_index_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "strata.json"
            c = fixture()
            c["catalog"][2]["config_path"] = str(p)
            for args in (["--pack"], ["--pack", "--mtp"], [None]):
                p.write_text(
                    json.dumps({"cwd": tmp, "exe": "component:engine", "args": args})
                )
                with patch.object(
                    discovery, "available_backends", return_value=["strata"]
                ):
                    errors, _ = manager.preflight(c, "strata")
                self.assertTrue(errors)

    def test_strata_does_not_require_unrelated_flash_setting(self):
        c = fixture()
        c["settings"]["flash_attention"]["value"] = False
        config.validate(c, "strata")
        with self.assertRaises(config.Error):
            config.validate(c, "llama.cpp")

    def test_runtime_path_cannot_inject_systemd_directive(self):
        c = fixture()
        c["runtime"]["home"] = "/home/user\nExecStart=bad"
        with self.assertRaises(config.Error):
            config.validate(c)

    def test_manifest_namespaces_and_registry_port(self):
        for source, path in [
            ("qwen3.8", "registry.ollama.ai/library/qwen3.8/latest"),
            ("team/model:q4", "registry.ollama.ai/team/model/q4"),
            ("localhost:5000/team/model:q4", "localhost:5000/team/model/q4"),
        ]:
            self.assertEqual(str(artifacts.manifest_relative(source)), path)

    def test_filtered_native_render_skips_other_broken_files(self):
        c = fixture()
        c["catalog"][2]["config_path"] = "/missing/strata.json"
        result = native(c, backends=["ollama"])
        self.assertNotIn("/etc/strata/llmsm.json", result)
        self.assertNotIn(
            "ExecStartPost", result["/etc/systemd/system/ollama.service.d/llm.conf"]
        )
        self.assertNotIn("/etc/llm-service-manager/ollama-boot.json", result)

    def test_services_have_no_manager_runtime_dependency(self):
        files = native(fixture(), lambda _: json.dumps(STRATA))
        for path, content in files.items():
            if path.endswith((".service", ".conf")):
                self.assertNotIn("boot.py", content)
                self.assertNotIn("serve.server", content)
                self.assertNotIn("/opt/llm-service-manager", content)
        self.assertIn(
            '/.local/bin/strata" "serve" "--profile"',
            files["/etc/systemd/system/llm-strata.service"],
        )

    def test_server_readiness_does_not_generate_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with patch.object(ops, "http", return_value={"status": "ok"}) as http:
                ops.ready(fixture(), "strata")
            self.assertEqual(http.call_count, 1)
            self.assertTrue(http.call_args.args[0].endswith("/health"))

    def test_explicit_apply_requests_preload_not_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            c = fixture()
            tag = "qwen3.8:latest"
            responses = [
                {},
                {},
                {"models": [{"name": tag, "size": 100, "size_vram": 100}]},
            ]
            with (
                patch.object(ops, "http", side_effect=responses) as http,
                patch.object(ops, "run"),
                patch.object(services, "program", return_value="/bin/ollama"),
            ):
                ops.ready(c, "ollama")
            body = http.call_args_list[1].args[1]
            self.assertNotIn("prompt", body)
            self.assertEqual(body["keep_alive"], "300s")

    def test_explicit_apply_rejects_cpu_spill(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(
                    ops,
                    "http",
                    side_effect=[
                        {},
                        {},
                        {
                            "models": [
                                {"name": "qwen3.8:latest", "size": 100, "size_vram": 99}
                            ]
                        },
                    ],
                ),
                patch.object(ops, "run"),
            ):
                with self.assertRaises(config.Error):
                    ops.ready(fixture(), "ollama")

    def test_disable_autostart_does_not_enable_any_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(
                    ops, "available_units", return_value=list(services.UNITS.values())
                ),
                patch.object(ops, "run") as run,
            ):
                ops.boot_target("ollama", False)
                self.assertTrue(
                    all(call.args[0][1] == "disable" for call in run.call_args_list)
                )

    def test_stop_fails_when_service_does_not_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(ops, "active", return_value=["ollama.service"]),
                patch.object(ops, "run"),
            ):
                with self.assertRaises(config.Error):
                    ops.stop()

    def test_systemd_bus_error_is_not_reported_as_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with patch.object(
                ops,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "bus unavailable"),
            ):
                with self.assertRaises(config.Error):
                    ops.active()

    def test_failed_atomic_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config"
            p.write_text("original")
            with patch.object(os, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    manager.atomic(p, "new")
            self.assertEqual(p.read_text(), "original")
            self.assertEqual(list(Path(tmp).iterdir()), [p])

    def test_missing_backend_preflight_has_no_side_effects(self):
        with (
            patch.object(preflight, "available_backends", return_value=[]),
            patch.object(manager, "Ops") as ops,
        ):
            errors, _ = manager.preflight(fixture(), "ollama")
            self.assertTrue(any("底座未安装" in e for e in errors))
            ops.assert_not_called()

    def test_actual_backend_probe_needs_executable_and_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = fixture()
            c["runtime"]["home"] = tmp
            home = Path(tmp)
            binpath = home / ".local/bin"
            binpath.mkdir(parents=True)
            for name in ["llama", "strata"]:
                (binpath / name).write_text("#!/bin/sh\n")
                (binpath / name).chmod(0o755)
            real = availability.program
            with patch.object(
                availability,
                "program",
                side_effect=lambda cfg, b: None if b == "ollama" else real(cfg, b),
            ):
                self.assertEqual(availability.available_backends(c), ["llama.cpp"])
                engine = home / ".local/share/strata/components/engine/test/strata"
                engine.parent.mkdir(parents=True)
                engine.write_text("engine")
                engine.chmod(0o755)
                self.assertEqual(
                    availability.available_backends(c), ["llama.cpp", "strata"]
                )
                (binpath / "llama").chmod(0o644)
                self.assertEqual(availability.available_backends(c), ["strata"])

    def test_api_invalid_shape_is_controlled_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(ops, "http", side_effect=[{}, {}, []]),
                patch.object(ops, "run"),
            ):
                with self.assertRaises(config.Error):
                    ops.ready(fixture(), "ollama")

    def test_clean_machine_first_menu_has_no_preexisting_config_or_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            c = config.normalized({}, home=tmp)
            with patch.object(discovery, "available_backends", return_value=[]):
                c = discovery.discover(c)
            self.assertEqual(c["catalog"], [])
            self.assertIsNone(c["target"])
            ui = Mock()
            ui.choose.side_effect = ["switch", "exit"]
            with (
                patch.object(tui, "read_snapshot", return_value=(c, "<absent>")),
                patch.object(tui, "discover", side_effect=lambda x: x),
                patch.object(tui, "available_backends", return_value=[]),
                patch.object(tui, "switch") as switch,
            ):
                tui.run_menu(ui)
            switch.assert_not_called()
            ui.text.assert_called_once()
            self.assertFalse(path.exists())
            self.assertIn("commands", [key for key, _ in tui.main_menu()])

    def test_no_services_stop_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = manager.Ops(tmp)
            with (
                patch.object(ops, "active", return_value=[]),
                patch.object(ops, "run") as run,
            ):
                ops.stop()
                run.assert_not_called()

    def test_discovery_without_programs_does_not_invent_models(self):
        c = fixture()
        with (
            patch.object(discovery, "available_backends", return_value=[]),
            patch.object(discovery, "strata_install_configs") as configs,
        ):
            discovery.discover(c)
            configs.assert_not_called()
