"""Shared language, terminal styling and output boundaries."""

import json

from inline_ui.style import (
    boundary as boundary,
)
from inline_ui.style import (
    cells as cells,
)
from inline_ui.style import (
    clipped as clipped,
)
from inline_ui.style import (
    render as render,
)
from inline_ui.style import (
    rich as rich,
)
from inline_ui.style import (
    section as section,
)

from . import __version__
from .paths import PREFERENCES as PREFERENCES

LANGUAGE = "zh"


def load_language():
    global LANGUAGE
    try:
        preferences = json.loads(PREFERENCES.read_text())
        LANGUAGE = (
            preferences.get("language", "zh") if isinstance(preferences, dict) else "zh"
        )
    except (OSError, ValueError):
        LANGUAGE = "zh"
    if LANGUAGE not in ("zh", "en"):
        LANGUAGE = "zh"


def t(zh, en):
    return en if LANGUAGE == "en" else zh


def set_language(language):
    global LANGUAGE
    if language not in ("zh", "en"):
        raise ValueError("Unsupported language")
    from .execution import Operations as Ops

    Ops(quiet=True).write(PREFERENCES, json.dumps({"language": language}) + "\n")
    LANGUAGE = language


LABELS = {
    "autostart": "Start selected backend at boot",
    "host": "Listen address",
    "port": "Port",
    "context": "Context per request",
    "kv": "KV precision",
    "parallel": "Concurrency",
    "idle_seconds": "Idle unload (seconds, 0 = disabled)",
    "vision": "GPU vision",
    "mtp": "MTP",
    "mtp_candidates": "MTP draft length",
    "mtp_kv": "MTP KV precision",
    "reserve_mib": "VRAM reserve (MiB)",
    "fit": "Automatic fit",
    "gpu_only": "100% GPU loading",
    "ple_io": "PLE memory policy",
    "lock_memory": "Allow PLE memory locking",
    "flash_attention": "Flash Attention",
}


def label(key):
    from .config import SPECS

    return t(SPECS[key][0], LABELS[key])


def formatted(value):
    return (
        t("开启", "On")
        if value is True
        else t("关闭", "Off")
        if value is False
        else str(value)
    )


VERSION = __version__
