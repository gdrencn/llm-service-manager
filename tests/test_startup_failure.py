"""Failure presentation is bounded and cannot interfere with rollback."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_config import FakeOps, fixture

from llm_service_manager import manager
from llm_service_manager.config import Error
from llm_service_manager.model_lifecycle import wait_router_loaded
from llm_service_manager.startup_failure import StartupFailure, capture, excerpt


class StartupFailureTests(unittest.TestCase):
    def test_router_status_not_dumped_into_error(self):
        ops = Mock()
        ops.http.return_value = {"data": [{"id": "model", "status": {
            "value": "unloaded", "failed": True, "exit_code": 1,
            "args": ["private-long-arguments"], "preset": "huge preset",
        }}]}
        with self.assertRaises(Error) as caught:
            wait_router_loaded(ops, "http://localhost", "model")
        self.assertIn("model", str(caught.exception))
        self.assertNotIn("preset", str(caught.exception))
        self.assertNotIn("private-long", str(caught.exception))

    def test_logs_scoped_to_this_invocation_and_time(self):
        ops = Mock(state=Path("/audit"))
        ops.run.side_effect = [
            subprocess.CompletedProcess([], 0, "a" * 32),
            subprocess.CompletedProcess([], 0, "I unrelated\nW no MTP layers\nE failed MTP context"),
        ]
        failure = capture(ops, fixture(), "llama.cpp", Error("load failed"), 123.5)
        self.assertIn("no MTP layers", str(failure))
        self.assertNotIn("unrelated", str(failure))
        argv = ops.run.call_args_list[1].args[0]
        self.assertIn("_SYSTEMD_INVOCATION_ID=" + "a" * 32, argv)
        self.assertIn("@123.5", argv)

    def test_no_valid_invocation_does_not_read_old_journal(self):
        ops = Mock(state=Path("/audit"))
        ops.run.return_value = subprocess.CompletedProcess([], 0, "0" * 32)
        failure = capture(ops, fixture(), "llama.cpp", Error("load failed"), 123)
        self.assertEqual(ops.run.call_count, 1)
        self.assertIn("未取得", str(failure))

    def test_diagnostic_failure_keeps_original_failure(self):
        ops = Mock(state=Path("/audit"))
        ops.run.side_effect = OSError("journal unavailable")
        failure = capture(ops, fixture(), "ollama", Error("original failure"), 123)
        self.assertIn("original failure", str(failure))

    def test_excerpt_is_bounded_and_removes_terminal_escape(self):
        output = excerpt("\n".join("\x1b[31mE failed " + "x" * 2000 for _ in range(100)))
        self.assertEqual(len(output.splitlines()), 6)
        self.assertLess(len(output), 2500)
        self.assertNotIn("\x1b", output)

    def test_failure_page_reports_actual_rollback_outcome(self):
        for broken in (False, True):
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as directory:
                ops = FakeOps(Path(directory) / "state")
                config = Path(directory) / "config.json"
                with (
                    patch.object(manager, "preflight", return_value=([], [])),
                    patch.object(manager, "available_backends", return_value=[]),
                    patch.object(manager, "native", return_value={}),
                    patch.object(ops, "ready", side_effect=Error("original failure")),
                    patch.object(manager, "recover", side_effect=Error("recovery failed") if broken else None) as recover,
                    self.assertRaises(StartupFailure) as caught,
                ):
                    manager.switch(fixture(), "ollama", "<absent>", ops, config)
                recover.assert_called_once_with(ops, lock=False)
                self.assertIn("original failure", str(caught.exception))
                self.assertIn("回滚未完成" if broken else "已恢复", str(caught.exception))
