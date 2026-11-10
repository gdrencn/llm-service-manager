"""Reusable inline terminal controls; no configuration or backend operations."""

import os
import select
import shutil
import sys
import termios
import threading
from concurrent.futures import Future
from contextlib import contextmanager

from .keyboard import Cancelled, read_key
from .style import Styled, boundary, cells, clean, render, rich


class UI:
    def __init__(self, translate=None):
        self.drawn = 0
        self.t = translate or (lambda zh, en: zh)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.finish()

    def interactive(self):
        return (
            sys.stdin.isatty()
            and sys.stdout.isatty()
            and os.environ.get("TERM") != "dumb"
        )

    def paint(self, lines):
        size = shutil.get_terminal_size()
        lines = [
            render(line, max(1, size.columns - 1))
            for line in lines[: max(1, size.lines - 1)]
        ]
        if self.drawn:
            sys.stdout.write(f"\x1b[{self.drawn}A")
        count = max(self.drawn, len(lines))
        for n in range(count):
            sys.stdout.write("\r\x1b[2K" + (lines[n] if n < len(lines) else "") + "\n")
        if count > len(lines):
            sys.stdout.write(f"\x1b[{count - len(lines)}A")
        self.drawn = len(lines)
        sys.stdout.flush()

    def clear(self):
        if self.drawn:
            self.paint([])

    def finish(self):
        self.clear()

    @contextmanager
    def keyboard(self):
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ICANON | termios.ECHO)
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, mode)
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
            yield
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, original)
            finally:
                sys.stdout.write("\x1b[?25h")
                sys.stdout.flush()

    def key(self):
        return read_key(sys.stdin.fileno())

    @contextmanager
    def input_cursor(self, prefix):
        """Show the native cursor on the last painted line; restore paint's anchor."""
        try:
            sys.stdout.write(f"\x1b[1A\r\x1b[{cells(clean(prefix)) + 1}G\x1b[?25h")
            sys.stdout.flush()
            yield
        finally:
            sys.stdout.write("\x1b[?25l\x1b[1B\r")
            sys.stdout.flush()

    def choose(self, title, rows, details=(), disabled=(), focus=0):
        if not rows:
            raise Cancelled
        if not self.interactive():
            print("\n" + title)
            for line in details:
                print(line)
            for n, (key, label) in enumerate(rows, 1):
                print(
                    f"{n}. {label}"
                    + (self.t(" [不可修改]", " [read-only]") if key in disabled else "")
                )
            while True:
                raw = input(
                    self.t("输入编号（留空返回）：", "Number (blank to return): ")
                ).strip()
                if not raw:
                    raise Cancelled
                if (
                    raw.isdigit()
                    and 1 <= int(raw) <= len(rows)
                    and rows[int(raw) - 1][0] not in disabled
                ):
                    return rows[int(raw) - 1][0]
                print(self.t("请选择有效编号。", "Choose a valid number."))
        i = max(0, min(focus, len(rows) - 1))
        with self.keyboard():
            while True:
                header = [
                    rich((title, "title")),
                    *details,
                    rich(
                        (
                            self.t(
                                "↑↓ 选择 · Enter 确认 · Esc 返回 · Ctrl+C 退出",
                                "↑↓ Select · Enter Confirm · Esc Back · Ctrl+C Exit",
                            ),
                            "muted",
                        )
                    ),
                ]
                size = shutil.get_terminal_size()
                height = min(len(rows), size.lines - len(header) - 2)
                if height < 1 or size.columns < 12:
                    self.paint(
                        [
                            self.t(
                                "请放大终端；Esc 返回。",
                                "Enlarge terminal; Esc to return.",
                            )
                        ]
                    )
                    self.key()  # Only cancellation/EOF may leave this branch.
                    continue
                start = max(0, min(i - height + 1, len(rows) - height))
                lines = []
                for n in range(start, start + height):
                    key, row_label = rows[n]
                    parts = (
                        row_label.parts
                        if isinstance(row_label, Styled)
                        else ((str(row_label), "muted" if key in disabled else ""),)
                    )
                    if n == i:
                        lines.append(rich(("› " + str(row_label), "highlight")))
                    else:
                        lines.append(Styled((("  ", ""),) + parts))
                self.paint(header + lines)
                key = self.key()
                if key in ("UP", "k"):
                    i = (i - 1) % len(rows)
                elif key in ("DOWN", "j"):
                    i = (i + 1) % len(rows)
                elif key in ("\n", "\r") and rows[i][0] not in disabled:
                    return rows[i][0]

    def blocks(
        self,
        title,
        rows,
        build,
        *,
        details=(),
        footer=(),
        disabled=(),
        focus=0,
        refresh=None,
        min_width=40,
    ):
        """Select a multi-line item. build(width, focus) returns one block per row.

        rows contains (key, plain-label) pairs for selection and plain-terminal fallback.
        Neither this control nor the renderer executes business operations.
        """
        if not rows:
            raise Cancelled
        i = max(0, min(focus, len(rows) - 1))
        if not self.interactive():
            return self.choose(title, rows, details, disabled, i)
        top = 0
        with self.keyboard():
            while True:
                size = shutil.get_terminal_size()
                blocks = build(size.columns, i)
                if len(blocks) != len(rows) or any(not block for block in blocks):
                    raise ValueError("Every item must have one nonempty display block")
                header = [rich((title, "title")), *details]
                ending = [*footer, rich((f"{i + 1}/{len(rows)}", "muted"))]
                budget = size.lines - len(header) - len(ending) - 1
                fits = len(blocks[i]) <= budget and size.columns >= min_width
                if not fits:
                    self.paint(
                        [
                            self.t(
                                "请放大终端以显示完整内容；Esc 返回。",
                                "Enlarge the terminal to show the complete item; Esc to return.",
                            )
                        ]
                    )
                else:
                    if top > i:
                        top = i
                    while sum(len(block) for block in blocks[top : i + 1]) > budget:
                        top += 1
                    body = []
                    for block in blocks[top:]:
                        if len(body) + len(block) > budget:
                            break
                        body.extend(block)
                    self.paint(header + body + ending)
                key = self.key()
                if key in ("UP", "k"):
                    i = (i - 1) % len(rows)
                elif key in ("DOWN", "j"):
                    i = (i + 1) % len(rows)
                elif key in ("r", "R") and refresh is not None:
                    return refresh
                elif key in ("\n", "\r") and fits and rows[i][0] not in disabled:
                    return rows[i][0]

    def document(self, title, lines, actions, *, focus=0):
        """Print all content into scrollback, then offer a small inline action menu."""
        self.clear()
        boundary(title)
        for line in lines:
            print(render(line))
        print(flush=True)
        return self.choose(self.t("操作", "Actions"), actions, focus=focus)

    def loading(self, title, work):
        """Show feedback immediately; work(cancel_event) must not write to the terminal."""
        cancel = threading.Event()
        message = self.t("检测中… Esc 返回", "Checking… Esc Back")
        if not self.interactive():
            self.clear()
            boundary(title)
            print(message, flush=True)
            try:
                return work(cancel)
            finally:
                cancel.set()
        result = Future()
        worker = None

        def run():
            try:
                result.set_result(work(cancel))
            except BaseException as ex:
                result.set_exception(ex)

        try:
            with self.keyboard():
                self.paint([rich((title, "title")), message])
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                while not result.done():
                    if select.select([sys.stdin.fileno()], [], [], 0.1)[0]:
                        self.key()  # Esc, EOF and Ctrl+C restore terminal state via finally.
                return result.result()
        finally:
            cancel.set()
            if worker is not None:
                worker.join(timeout=0.8)
            self.clear()

    def text(self, title, body):
        self.clear()
        boundary(title)
        print(str(body))
        input(self.t("\n按 Enter 返回…", "\nPress Enter to return…"))

    def input(self, title, default=""):
        if not self.interactive():
            raw = input(
                f"{title} [{default}]"
                + self.t("（输入 /cancel 取消）：", " (/cancel to cancel): ")
            )
            if raw == "/cancel":
                raise Cancelled
            return raw if raw else str(default)
        text = str(default)
        cursor = len(text)
        with self.keyboard():
            while True:
                size = shutil.get_terminal_size()
                if size.lines < 4 or size.columns < 8:
                    self.paint(
                        [
                            self.t(
                                "请放大终端；Esc 返回。",
                                "Enlarge terminal; Esc to return.",
                            )
                        ]
                    )
                    self.key()
                    continue
                width = max(1, (size.columns - 5) // 2)
                start = max(0, cursor - width + 1)
                self.paint(
                    [
                        rich((title, "title")),
                        self.t(
                            "Enter 确认 · Esc 取消 · Ctrl+U 清空",
                            "Enter Confirm · Esc Cancel · Ctrl+U Clear",
                        ),
                        text[start : start + width],
                    ]
                )
                with self.input_cursor(text[start:cursor]):
                    key = self.key()
                if key in ("\n", "\r"):
                    if text == "/cancel":
                        raise Cancelled
                    return text
                if key == "\x15":
                    text, cursor = "", 0
                elif key == "LEFT":
                    cursor = max(0, cursor - 1)
                elif key == "RIGHT":
                    cursor = min(len(text), cursor + 1)
                elif key == "HOME":
                    cursor = 0
                elif key == "END":
                    cursor = len(text)
                elif key == "DELETE":
                    text = text[:cursor] + text[cursor + 1 :]
                elif key in ("\x7f", "\b") and cursor:
                    text = text[: cursor - 1] + text[cursor:]
                    cursor -= 1
                elif len(key) == 1 and key.isprintable():
                    text = text[:cursor] + key + text[cursor:]
                    cursor += 1

    def external(self, fn, title=None):
        self.clear()
        boundary(title or self.t("执行结果", "Execution output"))
        fn()
        input(self.t("\n按 Enter 返回菜单…", "\nPress Enter to return to the menu…"))
