"""Exercise real terminal input and ensure menus preserve terminal ownership."""

import os
import pty
import re
import select
import subprocess
import sys
import time
import unittest


def interact(argv, steps, timeout=8):
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"),
    )
    os.close(slave)
    output = b""
    position = 0
    try:
        for marker, keys in steps:
            deadline = time.monotonic() + timeout
            while marker not in output[position:]:
                if time.monotonic() > deadline:
                    raise AssertionError(
                        ("prompt timeout", marker, output.decode(errors="replace"))
                    )
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        raise AssertionError(
                            ("terminal closed", output.decode(errors="replace"))
                        )
                    output += chunk
            position = output.index(marker, position) + len(marker)
            os.write(master, keys)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            if proc.poll() is not None:
                break
        proc.wait(timeout=1)
        return proc.returncode, output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)


class InlineTests(unittest.TestCase):
    def test_select_and_terminal_restored(self):
        script = """import termios,sys
from inline_ui import UI
before=termios.tcgetattr(0)
result=UI().choose('Example',[('a','Alpha'),('b','Beta')])
assert termios.tcgetattr(0)==before
print('RESULT',result)
"""
        code, out = interact([sys.executable, "-c", script], [(b"Alpha", b"\x1b[B\r")])
        self.assertEqual(code, 0)
        self.assertIn(b"RESULT b", out)
        self.assertIn(b"\x1b[?25l", out)
        self.assertIn(b"\x1b[?25h", out)
        self.assertLess(out.rfind(b"\x1b[?25l"), out.rfind(b"\x1b[?25h"))
        self.assertNotIn(b"\x1b[?1049", out)
        self.assertNotIn(b"\x1b[2J", out)

    def test_escape_restores_terminal(self):
        script = """import termios
from inline_ui import UI,Cancelled
before=termios.tcgetattr(0)
try:UI().choose('Example',[('a','Alpha')])
except Cancelled:pass
else:raise AssertionError('not cancelled')
assert termios.tcgetattr(0)==before
print('RESTORED')
"""
        code, out = interact([sys.executable, "-c", script], [(b"Alpha", b"\x1b")])
        self.assertEqual(code, 0)
        self.assertIn(b"RESTORED", out)
        self.assertIn(b"\x1b[?25l", out)
        self.assertIn(b"\x1b[?25h", out)
        self.assertLess(out.rfind(b"\x1b[?25l"), out.rfind(b"\x1b[?25h"))


class RefreshTests(unittest.TestCase):
    def test_shared_area_and_history_boundary(self):
        import io
        import re
        from unittest.mock import patch

        from inline_ui import UI

        output = io.StringIO()
        lines = ["earlier shell output", ""]
        row = 1
        column = 0

        def screen():
            nonlocal row, column
            data = output.getvalue()
            output.seek(0)
            output.truncate(0)
            for token in re.findall(r"\x1b\[[0-9;]*[A-Za-z]|[^\x1b]", data):
                if token.startswith("\x1b["):
                    if token.endswith("A"):
                        row -= int(token[2:-1])
                        self.assertGreaterEqual(row, 1)
                    elif token == "\x1b[2K":
                        lines[row] = ""
                    else:
                        self.fail("unexpected screen escape " + repr(token))
                elif token == "\r":
                    column = 0
                elif token == "\n":
                    row += 1
                    column = 0
                    while len(lines) <= row:
                        lines.append("")
                else:
                    line = lines[row]
                    lines[row] = line[:column] + token + line[column + 1 :]
                    column += 1

        with patch("sys.stdout", output):
            ui = UI()
            ui.paint(["Main", "choice 1", "choice 2"])
            screen()
            ui.paint(["Submenu", "Back"])
            screen()
            self.assertEqual(lines[:4], ["earlier shell output", "Submenu", "Back", ""])
            ui.paint(["Configuration", "a", "b", "c"])
            screen()
            self.assertEqual(row, 5)
            ui.clear()
            screen()
            print("permanent execution log")
            screen()
            ui.paint(["Main", "Exit"])
            screen()
            ui.finish()
            screen()
        self.assertEqual(lines[0], "earlier shell output")
        self.assertEqual(lines[1], "permanent execution log")
        self.assertTrue(all(not line for line in lines[2:]))

    def test_inline_edit_and_return(self):
        script = """from inline_ui import UI
ui=UI()
ui.choose('PARENT',[('x','Open')])
value=ui.input('EDIT','128k')
ui.choose('RETURN',[('x','Exit')])
ui.finish()
assert value=='256k',value
print('DONE')
"""
        code, out = interact(
            [sys.executable, "-c", script],
            [(b"Open", b"\r"), (b"128k", b"\x15256k\r"), (b"RETURN", b"\r")],
        )
        self.assertEqual(code, 0)
        self.assertIn(b"DONE", out)
        self.assertIn(b"\x1b[3A\r\x1b[2KEDIT", re.sub(rb"\x1b\[[0-9;]*m", b"", out))
        self.assertNotIn(b"\x1b[2J", out)
