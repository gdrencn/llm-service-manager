"""Malformed persistence and upgrade rollback must fail safely before mutation."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_config import fixture

from llm_service_manager import config, services, transaction


class IntegrityTests(unittest.TestCase):
    def test_model_file_fields_reject_non_string_even_when_falsey(self):
        for backend, field in [("llama.cpp", "path"), ("strata", "config_path")]:
            for value in [None, 0, False, [], {}, ""]:
                c = fixture()
                next(m for m in c["catalog"] if m["backend"] == backend)[field] = value
                with (
                    self.subTest(backend=backend, value=value),
                    self.assertRaises(config.Error),
                ):
                    config.validate(config.normalized(c))
        c = fixture()
        c["catalog"][1]["vision_path"] = []
        with self.assertRaises(config.Error):
            config.validate(c)

    def test_schema_bool_is_not_a_supported_version(self):
        for version in [True, False, 1.0, "2"]:
            with self.assertRaises(config.Error):
                config.normalized({"schema": version})

    def test_recovery_requires_config_for_running_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "pending.json"
            p.write_text(
                json.dumps({"active": ["ollama.service"], "enabled": {}, "files": {}})
            )
            with self.assertRaisesRegex(config.Error, "配置快照"):
                transaction.read_recovery(p)

    def test_router_restore_still_validates_model_loaded(self):
        ops = services.Services()
        with (
            patch.object(services, "read_config", return_value=fixture()),
            patch.object(
                services.Path,
                "read_text",
                return_value="ExecStart=llama serve --models-preset config.ini",
            ),
            patch.object(ops, "ready") as ready,
        ):
            ops.restored(
                {
                    "active": ["llama-server-local.service"],
                    "runtime": {"backend": "llama.cpp", "loaded": True},
                }
            )
            ready.assert_called_once()

    def test_gpu_confirmation_rejects_missing_invocation_and_zero_layers(self):
        ops = services.Services()
        for invocation in ["", "unknown", "0" * 32]:
            log = "offloaded 0/0 layers to GPU"
            with patch.object(
                ops,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, invocation),
                    subprocess.CompletedProcess([], 0, log),
                ],
            ):
                with self.assertRaises(config.Error):
                    ops.check_gpu_offload("unit")

    def test_gpu_confirmation_uses_current_invocation_only(self):
        ops = services.Services()
        with patch.object(
            ops,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "a" * 32),
                subprocess.CompletedProcess([], 0, "offloaded 66/66 layers to GPU"),
            ],
        ) as run:
            ops.check_gpu_offload("unit")
            self.assertIn("_SYSTEMD_INVOCATION_ID=" + "a" * 32, run.call_args.args[0])
