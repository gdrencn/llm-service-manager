"""Only the current apply operation is compensated; no historical migrations."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import FakeOps, fixture

from llm_service_manager import manager, services
from llm_service_manager.config import Error
from llm_service_manager.config_document import encode
from llm_service_manager.model_transaction import rollback_config, runtime_snapshot
from llm_service_manager.paths import ALL_UNITS, UNITS


class ModelOps(FakeOps):
    def __init__(self, state):
        super().__init__(state)
        self.running = []
        self.model_value = "original"
        self.restores = 0
        self.fail_compensation = False
        self.fail_apply = True

    def enabled(self):
        return {}

    def ready(self, c, b):
        self.model_value = "candidate"

    def boot_target(self, *args):
        if self.fail_apply:
            raise Error("injected post-create failure")

    def restore_ollama(self, record):
        self.restores += 1
        if self.fail_compensation:
            raise Error("injected compensation failure")
        self.model_value = "original"


class ApplyTransactionTests(unittest.TestCase):
    def test_current_units_only(self):
        self.assertEqual(set(ALL_UNITS), set(UNITS.values()))
        self.assertEqual(len(ALL_UNITS), 3)

    def run_case(self, fail_compensation=False, fail_restore_file=False, fresh=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            if not fresh:
                config.write_text(json.dumps(encode(fixture())))
            before = None if fresh else config.read_bytes()
            created = root / "created.ini"
            ops = ModelOps(root / "state")
            ops.fail_compensation = fail_compensation
            with (
                patch.object(manager, "preflight", return_value=([], [])),
                patch.object(
                    manager, "native", return_value={str(created): "candidate"}
                ),
            ):
                if fail_restore_file:
                    with patch.object(
                        manager, "restore_file", side_effect=OSError("disk unavailable")
                    ):
                        with self.assertRaises(Error):
                            manager.switch(
                                fixture(),
                                "ollama",
                                manager.revision(config),
                                ops,
                                config,
                            )
                else:
                    with self.assertRaises(Error):
                        manager.switch(
                            fixture(), "ollama", manager.revision(config), ops, config
                        )
            if fail_compensation or fail_restore_file:
                self.assertTrue((ops.state / "pending.json").exists())
                ops.fail_compensation = False
                manager.recover(ops)
            self.assertEqual(config.read_bytes() if config.exists() else None, before)
            self.assertFalse(created.exists())
            self.assertEqual(ops.model_value, "original")
            self.assertEqual(ops.running, [])
            self.assertFalse((ops.state / "pending.json").exists())
            self.assertEqual(ops.restores, 2 if fail_compensation else 1)
            record = json.loads((ops.state / "last-rollback.json").read_text())
            self.assertTrue(record["ollama_restored"])

    def test_post_create_failure_restores_model_and_removes_new_file(self):
        self.run_case()

    def test_first_apply_failure_leaves_no_manager_configuration(self):
        self.run_case(fresh=True)

    def test_failed_compensation_is_pending_and_retryable(self):
        self.run_case(fail_compensation=True)

    def test_retry_after_compensation_does_not_reapply_model(self):
        self.run_case(fail_restore_file=True)

    def test_new_unit_links_are_removed_before_unit_disappears(self):
        ops = services.Services()
        with (
            patch.object(ops, "available_units", return_value=list(ALL_UNITS)),
            patch.object(ops, "run") as run,
        ):
            ops.clear_new_boot_links({"enabled": {"ollama.service": "enabled"}})
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["systemctl", "disable", UNITS["llama.cpp"]],
                ["systemctl", "disable", UNITS["strata"]],
            ],
        )

    def test_unknown_native_service_does_not_probe_assumed_port(self):
        ops = Mock()
        with patch(
            "llm_service_manager.model_transaction.read_config",
            return_value={"target": None},
        ):
            self.assertIsNone(runtime_snapshot("/path", ["ollama.service"], ops))
        ops.http.assert_not_called()

    def test_unloaded_state_is_preserved_on_restore(self):
        c = fixture()
        c["target"] = {"backend": "llama.cpp", "model": "l"}
        ops = services.Services()
        with (
            patch.object(services, "read_config", return_value=c),
            patch.object(ops, "ready") as ready,
            patch("llm_service_manager.model_lifecycle.release") as release,
        ):
            ops.restored(
                {
                    "active": [UNITS["llama.cpp"]],
                    "runtime": {"backend": "llama.cpp", "loaded": False},
                }
            )
        ready.assert_called_once_with(
            c, "llama.cpp", unit=UNITS["llama.cpp"], configure=False
        )
        release.assert_called_once_with(c, ops)

    def test_restore_does_not_run_create_again(self):
        ops = services.Services()
        with (
            patch.object(ops, "wait_ready", return_value="http://localhost"),
            patch.object(ops, "create_ollama_model") as create,
            patch.object(
                ops,
                "http",
                side_effect=[
                    {},
                    {
                        "models": [
                            {"name": "qwen3.8:latest", "size": 100, "size_vram": 100}
                        ]
                    },
                ],
            ),
        ):
            ops.ready(fixture(), "ollama", configure=False)
        create.assert_not_called()

    def test_model_compensation_record_validation(self):
        for record in [
            None,
            {},
            {"name": "x", "host": "0.0.0.0", "port": 0, "modelfile": "FROM /blob"},
            {"name": "x", "host": "0.0.0.0", "port": 55555, "modelfile": "broken"},
        ]:
            with self.assertRaises((ValueError, Error)):
                rollback_config(record)
