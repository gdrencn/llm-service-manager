"""Regression cases for disposable runtime, fresh machines and transactional boundaries."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import STRATA, FakeOps, fixture

from llm_service_manager import (
    config,
    manager,
    services,
    storage,
)
from llm_service_manager.adapters import native
from llm_service_manager.config_document import encode
from llm_service_manager.execution import Operations


class QualityTests(unittest.TestCase):
    def test_native_files_have_no_disposable_directory_dependencies(self):
        files = native(fixture(), lambda _: json.dumps(STRATA))
        for path, content in files.items():
            self.assertNotIn("/etc/llm-service-manager", path)
            for prefix in (
                "/opt/llm-service-manager",
                "/etc/llm-service-manager",
                "/var/lib/llm-service-manager",
            ):
                self.assertNotIn(prefix, content)
        self.assertIn("/etc/strata/strata-local.json", files)

    def test_atomic_write_preserves_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config"
            p.write_text("before")
            p.chmod(0o600)
            storage.atomic(p, "after")
            self.assertEqual(p.read_text(), "after")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_snapshot_hashes_the_bytes_that_were_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config"
            raw = b'{"ollama":{"settings":{"context":{"value":8192}}}}'
            p.write_bytes(raw)
            original = Path.read_bytes

            def racing_read(path):
                data = original(path)
                if path == p:
                    p.write_text("{}")
                return data

            with patch.object(Path, "read_bytes", racing_read):
                cfg, revision = storage.read_snapshot(p)
            self.assertEqual(config.value(cfg, "context"), 8192)
            self.assertEqual(revision, hashlib.sha256(raw).hexdigest())
            self.assertNotEqual(revision, storage.revision(p))

    def test_invalid_recovery_is_rejected_before_service_stop(self):
        for data in ("not-base64", 42):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                ops = FakeOps(Path(tmp))
                p = Path(tmp) / "pending.json"
                p.write_text(
                    json.dumps(
                        {
                            "active": ["ollama.service"],
                            "files": {"/tmp/not-written": data},
                        }
                    )
                )
                with patch.object(ops, "stop") as stop:
                    with self.assertRaises(config.Error):
                        manager.recover(ops)
                stop.assert_not_called()
                self.assertTrue(p.exists())

    def test_commit_audit_failure_does_not_roll_back_running_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config"
            p.write_text(json.dumps(encode(fixture())))
            ops = FakeOps(Path(tmp) / "state")

            def event(kind, **data):
                if kind == "transaction-committed":
                    raise OSError("log full")

            ops.event = event
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(manager, "native", return_value={}),
            ):
                manager.switch(fixture(), "ollama", storage.revision(p), ops, p)
            self.assertEqual(json.loads(p.read_text())["target"]["backend"], "ollama")
            self.assertFalse((ops.state / "pending.json").exists())
            self.assertFalse((ops.state / "last-rollback.json").exists())

    def test_restore_unconfigured_native_service_does_not_guess_api_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp)
            with (
                patch.object(
                    services, "read_config", return_value=config.normalized({})
                ),
                patch.object(
                    ops,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, "active\n"),
                ) as run,
                patch.object(ops, "http") as http,
                patch.object(ops, "ready") as ready,
            ):
                ops.restored({"active": ["ollama.service"], "runtime": None})
            run.assert_called_once_with(["systemctl", "is-active", "ollama.service"])
            http.assert_not_called()
            ready.assert_not_called()

    def test_process_exits_during_readiness_fail_promptly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp)
            with (
                patch.object(ops, "http", side_effect=OSError("connection refused")),
                patch.object(
                    ops,
                    "run",
                    return_value=subprocess.CompletedProcess([], 3, "inactive\n"),
                ),
                patch("time.sleep") as sleep,
            ):
                with self.assertRaises(config.Error):
                    ops.wait_ready(fixture(), "strata")
            sleep.assert_not_called()

    def test_settings_parser_shared_by_cli_and_inline_form(self):
        self.assertEqual(config.parse_setting("context", "128K"), 131072)
        self.assertFalse(config.parse_setting("autostart", "false"))
        for key, value in [
            ("context", "12.5"),
            ("autostart", "yes"),
            ("kv", "invalid"),
        ]:
            with self.assertRaises(config.Error):
                config.parse_setting(key, value)

    def test_unsupported_configuration_is_a_controlled_error(self):
        for raw in (
            {"schema": 99},
            {"models": {"ollama": 42}},
            {"settings": {"unknown": {"value": 1}}},
        ):
            with self.assertRaises(config.Error):
                config.normalized(raw)

    def test_existing_auth_configuration_is_never_silently_removed(self):
        with self.assertRaises(config.Error):
            native(
                fixture(),
                lambda _: json.dumps(dict(STRATA, api_key="keep-me")),
                backends=["strata"],
            )

    def test_systemd_home_preserves_dollar_without_exec_expansion(self):
        c = fixture()
        c["runtime"]["home"] = "/home/a$b"
        unit = native(c, backends=["llama.cpp"])[
            "/etc/systemd/system/llama-server-local.service"
        ]
        self.assertIn('Environment="HOME=/home/a$b"', unit)
        self.assertIn('"/home/a$$b/.local/bin/llama"', unit)

    def test_root_installation_identity_is_supported(self):
        cfg = config.normalized({}, user="root")
        config.validate(cfg)
        self.assertEqual(cfg["runtime"]["user"], "root")

    def test_failed_command_is_not_printed_twice(self):
        import io

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ops = Operations(tmp)
            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "one failure"),
            ):
                with self.assertRaisesRegex(config.Error, "one failure"):
                    ops.run(["false"])
            self.assertNotIn("one failure", stdout.getvalue())

    def test_installer_generated_sources_are_synchronized_without_execution(self):
        root = Path(__file__).resolve().parent.parent
        subprocess.run(
            ["python3", str(root / "scripts/build_installers.py"), "--check"],
            check=True,
        )
        for name in [
            "bootstrap.sh",
            "install.sh",
            "test/bootstrap.sh",
            "test/install.sh",
        ]:
            subprocess.run(["sh", "-n", str(root / name)], check=True)

    def test_ollama_has_no_private_runner_passthrough(self):
        files = native(fixture(), backends=["ollama"])
        self.assertNotIn(
            "LLAMA_ARG_",
            files["/etc/systemd/system/ollama.service.d/ollama-local.conf"],
        )
        self.assertNotIn("ollama", config.SPECS["fit"][2])
        self.assertNotIn("ollama", config.SPECS["mtp_kv"][2])

    def test_managed_symlink_is_rejected_before_stopping_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "config"
            p.write_text(json.dumps(encode(fixture())))
            target = root / "external"
            target.write_text("unchanged")
            link = root / "unit"
            link.symlink_to(target)
            ops = FakeOps(root / "state")
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(manager, "native", return_value={str(link): "new"}),
                patch.object(ops, "stop") as stop,
            ):
                with self.assertRaises(config.Error):
                    manager.switch(fixture(), "ollama", storage.revision(p), ops, p)
            stop.assert_not_called()
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "unchanged")

    def test_status_reports_bus_failure_instead_of_stopped(self):
        from llm_service_manager import status

        ops = Mock()
        ops.run.return_value = subprocess.CompletedProcess([], 1, "bus unavailable")
        with patch.object(status, "available_backends", return_value=["ollama"]):
            snap = status.collect(fixture(), ops)
        self.assertEqual(snap["services"], {})
        self.assertTrue(snap["errors"])
        ops.http.assert_not_called()

    def test_foreign_listener_is_not_mistaken_for_selected_service(self):
        import socket

        with tempfile.TemporaryDirectory() as tmp, socket.socket() as foreign:
            foreign.bind(("127.0.0.1", 0))
            foreign.listen(1)
            c = fixture()
            c["settings"]["port"]["value"] = foreign.getsockname()[1]
            with self.assertRaises(config.Error):
                services.Services(tmp).check_bind(c)
            self.assertGreater(foreign.fileno(), 0)

    def test_runtime_directories_are_created_as_service_user(self):
        from llm_service_manager.paths import STRATA_CONFIG

        with tempfile.TemporaryDirectory() as tmp:
            ops = services.Services(tmp)
            c = fixture()
            with patch.object(ops, "run") as run:
                ops.prepare_runtime(c, {STRATA_CONFIG: "{}"})
                self.assertEqual(
                    run.call_args.args[0][:6],
                    ["sudo", "-H", "-u", c["runtime"]["user"], "--", "mkdir"],
                )
                run.reset_mock()
                ops.prepare_runtime(c, {})
                run.assert_not_called()
