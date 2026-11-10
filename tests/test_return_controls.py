"""Return actions use menu input; routine checks remain fully audited."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_inline import interact

from inline_ui import UI
from llm_service_manager.execution import Operations


class ReturnControlTests(unittest.TestCase):
    def test_text_and_external_ignore_typing_and_restore_terminal(self):
        for operation in (
            "ui.text('Result', 'KEPT OUTPUT')",
            "ui.external(lambda: print('KEPT OUTPUT'))",
        ):
            with self.subTest(operation=operation):
                script = f"""import termios
from inline_ui import UI
before=termios.tcgetattr(0)
with UI(translate=lambda zh,en:en) as ui:
 {operation}
assert termios.tcgetattr(0)==before
print('RETURNED')
"""
                code, output = interact(
                    [sys.executable, "-c", script], [(b"Back", b"unwanted987\r")]
                )
                self.assertEqual(code, 0, output)
                self.assertIn(b"KEPT OUTPUT", output)
                self.assertIn(b"RETURNED", output)
                self.assertNotIn(b"unwanted987", output)
                self.assertNotIn(b"Press Enter to return", output)

    def test_escape_returns_without_turning_success_into_cancel(self):
        script = """from inline_ui import UI
with UI(translate=lambda zh,en:en) as ui:
 ui.text('Result','BODY')
print('RETURNED')
"""
        code, output = interact([sys.executable, "-c", script], [(b"Back", b"\x1b")])
        self.assertEqual(code, 0, output)
        self.assertIn(b"RETURNED", output)

    def test_plain_menu_blank_returns(self):
        with (
            patch.object(UI, "interactive", return_value=False),
            patch("builtins.input", return_value=""),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            UI().back()

    def test_hidden_queries_remain_in_audit_and_actions_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = Operations(Path(directory))
            output = io.StringIO()
            commands = [
                ["systemctl", "show", "ollama.service"],
                ["systemctl", "is-active", "ollama.service"],
                ["systemctl", "is-enabled", "ollama.service"],
                ["journalctl", "--no-pager"],
                ["systemctl", "stop", "ollama.service"],
            ]
            with contextlib.redirect_stdout(output):
                ops.event("progress", message="Checking services…")
                for command in commands:
                    ops.event("command", argv=command)
            self.assertIn("Checking services", output.getvalue())
            self.assertIn("systemctl stop", output.getvalue())
            self.assertNotIn("is-active", output.getvalue())
            self.assertNotIn("journalctl", output.getvalue())
            rows = [
                json.loads(line)
                for line in (Path(directory) / "audit.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row["argv"] for row in rows if row["kind"] == "command"], commands
            )
