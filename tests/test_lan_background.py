"""Reusable loading UI and cancellable subprocess cleanup."""

import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch

from test_inline import interact

from llm_service_manager.execution import cancellable_run


class BackgroundTests(unittest.TestCase):
    def test_cancelled_subprocess_is_reaped(self):
        cancel = threading.Event()
        spawned = []
        popen = subprocess.Popen

        def spawn(*a, **kw):
            p = popen(*a, **kw)
            spawned.append(p)
            return p

        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            with patch(
                "llm_service_manager.execution.subprocess.Popen", side_effect=spawn
            ):
                with self.assertRaises(CancelledError):
                    cancellable_run(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        cancel,
                        10,
                    )
        finally:
            timer.cancel()
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())

    def test_loading_is_visible_and_escape_cancels_without_waiting_for_query(self):
        script = """import threading,termios,time
from inline_ui import UI,Cancelled
before=termios.tcgetattr(0)
done=threading.Event()
def query(cancel):
 assert cancel.wait(30)
 done.set()
with UI() as ui:
 try:ui.loading('LAN',query)
 except Cancelled:pass
 else:raise AssertionError('not cancelled')
assert done.wait(1)
assert termios.tcgetattr(0)==before
print('RESPONSIVE')
"""
        start = time.monotonic()
        rc, out = interact(
            [sys.executable, "-c", script], [("检测中".encode(), b"\x1b")]
        )
        self.assertEqual(rc, 0, out)
        self.assertIn(b"RESPONSIVE", out)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn(b"\x1b[?25h", out)

    def test_loading_error_restores_terminal_and_propagates(self):
        script = """import termios
from inline_ui import UI
before=termios.tcgetattr(0)
def fail(cancel):raise ValueError('query failed')
try:
 with UI() as ui:ui.loading('LAN',fail)
except ValueError:pass
else:raise AssertionError('swallowed failure')
assert termios.tcgetattr(0)==before
print('RESTORED')
"""
        rc, out = interact([sys.executable, "-c", script], [])
        self.assertEqual(rc, 0, out)
        self.assertIn(b"RESTORED", out)
