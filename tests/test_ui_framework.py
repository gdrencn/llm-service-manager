"""Framework contract: portable package, generic controls and callback ownership."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_inline import interact

from inline_ui import UI, Cancelled


class FrameworkTests(unittest.TestCase):
    def test_package_works_without_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(
                Path(__file__).resolve().parents[1] / "inline_ui",
                Path(tmp) / "inline_ui",
            )
            code = """import sys
from inline_ui import UI, rich, render
assert not any(m.startswith('llm_service_manager') for m in sys.modules)
assert render(rich(('hello','title'))) == 'hello'
assert UI(translate=lambda zh,en:en).t('中','English') == 'English'
"""
            env = dict(os.environ, PYTHONPATH=tmp, NO_COLOR="1")
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_generic_blocks_skip_disabled_and_return_key(self):
        script = """from inline_ui import UI
with UI() as ui:
 value=ui.blocks('GENERIC', [('locked','Locked'),('run','Run')],
  lambda width,focus: [['Locked'],['Run']], disabled=['locked'])
 assert value=='run',value
print('DONE')
"""
        rc, out = interact([sys.executable, "-c", script], [(b"Locked", b"\r\x1b[B\r")])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"DONE", out)

    def test_refresh_is_a_return_value_not_an_operation(self):
        script = """from inline_ui import UI
with UI() as ui:
 assert ui.blocks('Refreshable',[('x','Item')],lambda w,i:[['Item']],refresh='refresh')=='refresh'
print('DONE')
"""
        rc, out = interact([sys.executable, "-c", script], [(b"Item", b"r")])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"DONE", out)

    def test_renderer_error_restores_terminal(self):
        script = """import termios
from inline_ui import UI
before=termios.tcgetattr(0)
try:
 with UI() as ui:ui.blocks('Bad',[('x','X')],lambda w,i:[])
except ValueError:pass
else:raise AssertionError('accepted invalid renderer')
assert before==termios.tcgetattr(0)
print('RESTORED')
"""
        rc, out = interact([sys.executable, "-c", script], [])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"RESTORED", out)
        self.assertIn(b"\x1b[?25h", out)

    def test_too_small_terminal_never_selects_hidden_content(self):
        script = """import os
from inline_ui import UI,Cancelled
os.environ['LINES']='5'
os.environ['COLUMNS']='20'
with UI(translate=lambda zh,en:en) as ui:
 try:ui.blocks('Too small',[('x','X')],lambda w,i:[['line']*10])
 except Cancelled:pass
 else:raise AssertionError('selected unreadable content')
print('CANCELLED')
"""
        rc, out = interact([sys.executable, "-c", script], [(b"Enlarge", b"\r\x1b")])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"CANCELLED", out)

    def test_callback_runs_after_ui_is_cleared_and_errors_propagate(self):
        ui = UI()
        order = []
        with (
            patch.object(ui, "clear", side_effect=lambda: order.append("clear")),
            patch("inline_ui.controls.boundary"),
        ):

            def callback():
                order.append("callback")
                raise RuntimeError("business failure")

            with self.assertRaisesRegex(RuntimeError, "business failure"):
                ui.external(callback)
        self.assertEqual(order, ["clear", "callback"])

    def test_plain_fallback_respects_disabled_items(self):
        with (
            patch.object(UI, "interactive", return_value=False),
            patch("builtins.input", side_effect=["1", "2"]),
            patch("builtins.print"),
        ):
            value = UI().blocks(
                "Plain",
                [("locked", "Locked"), ("run", "Run")],
                lambda w, i: [],
                disabled=["locked"],
            )
        self.assertEqual(value, "run")

    def test_context_manager_finishes_on_cancellation(self):
        ui = UI()
        with patch.object(ui, "finish") as finish:
            with self.assertRaises(Cancelled):
                with ui:
                    raise Cancelled
        finish.assert_called_once()
