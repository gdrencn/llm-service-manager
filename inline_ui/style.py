"""Semantic terminal styles and safe width-aware rendering; standard library only."""

import os
import sys
import unicodedata
from dataclasses import dataclass


def color_enabled():
    return (
        sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    )


def clean(text):
    return "".join(
        c
        for c in str(text).replace("\n", " ")
        if not unicodedata.category(c).startswith("C")
    )


def cells(text):
    return sum(
        2
        if unicodedata.east_asian_width(c) in ("W", "F")
        else 0
        if unicodedata.combining(c)
        else 1
        for c in text
    )


def clipped(text, width):
    result = ""
    for c in clean(text):
        if cells(result + c) > width:
            break
        result += c
    return result


# Codex dark Markdown theme: bold=#F38BA8, inline code=#A6E3A1, links=#89B4FA.
# https://github.com/catppuccin/bat/blob/main/themes/Catppuccin%20Mocha.tmTheme
ACCENT = "38;2;243;139;168"
GREEN = "38;2;166;227;161"
LINK = "38;2;137;180;250"
COLORS = {
    "accent": ACCENT,
    "section": "1;" + ACCENT,
    "group": "1;37",
    "plain": "0;37",
    "highlight": "1;30;46",
    "title": "1;36",
    "key": "37",
    "value": "1;37",
    "command": ACCENT,
    "action": "0;37",
    "muted": "2",
    "good": GREEN,
    "link": LINK,
    "warn": "33",
    "error": "31",
}


@dataclass(frozen=True)
class Styled:
    parts: tuple

    def __str__(self):
        return "".join(str(s) for s, _ in self.parts)


def rich(*parts):
    return Styled(tuple(parts))


def render(text, width=100000):
    parts = text.parts if isinstance(text, Styled) else ((str(text), ""),)
    result = ""
    for part, tone in parts:
        part = clipped(part, width)
        width -= cells(part)
        result += (
            f"\x1b[{COLORS[tone]}m{part}\x1b[0m"
            if tone in COLORS and color_enabled()
            else part
        )
    return result


def boundary(title, tone="title"):
    print("\n" + render(rich(("─" * 48, "muted"))) + "\n")
    print(render(rich((title, tone))) + "\n", flush=True)


def section(title):
    print("\n" + render(rich((title, "section"))))


def wrap(text, width):
    text = str(text)
    out = []
    while text:
        part = clipped(text, width)
        if not part:
            break
        out.append(part)
        text = text[len(part) :]
    return out or [""]
