"""Installation inventory shares artifacts with model discovery and preflight."""

import json
import re
from pathlib import Path

from .artifacts import (
    complete_asset,
    embedded_mtp,
    gguf,
    gguf_set,
    strata_assets,
)
from .availability import executable, program
from .commands import recipe_specs
from .ollama_store import complete_ollama
from .paths import hf_cache, ollama_roots, strata_engines, strata_install


def ollama_model(home, roots=None):
    roots = ollama_roots(home) if roots is None else roots
    return any(complete_ollama(root, "qwen3.8:latest") for root in roots)


def llama_model(c, home):
    paths = set(
        (hf_cache(home)).glob(
            "models--unsloth--Qwen3.8-27B-GGUF/snapshots/*/*UD-Q4_K_XL*.gguf"
        )
    )
    known = {
        m.get("path"): m
        for m in c.get("catalog", [])
        if m.get("backend") == "llama.cpp"
    }
    paths.update(
        Path(p)
        for p in known
        if p and "Qwen3.8-27B" in Path(p).name and "UD-Q4_K_XL" in Path(p).name
    )
    for p in paths:
        if re.search(r"-\d{5}-of-\d{5}", p.name) and "-00001-of-" not in p.name:
            continue
        m = known.get(str(p), {})
        vision = [m.get("vision_path", ""), *p.parent.glob("*mmproj*.gguf")]
        mtp = [
            m.get("mtp_path", ""),
            *p.parent.glob("*mtp*.gguf"),
            *p.parent.glob("*MTP*.gguf"),
        ]
        if (
            gguf_set(p)
            and any(gguf(v) for v in vision)
            and (any(gguf(v) for v in mtp) or embedded_mtp(p))
        ):
            return True
    return False


def strata_model(home):
    try:
        cfg = json.loads((strata_install(home) / "strata-nvfp4-nvfp4.json").read_text())
        return all(complete_asset(p) for p in strata_assets(cfg))
    except (OSError, ValueError, TypeError):
        return False


def inventory(c):
    home = Path(c["runtime"]["home"])

    engines = strata_engines(home)
    checks = {
        "ollama.install": lambda: program(c, "ollama") is not None,
        "ollama.model": lambda: ollama_model(home),
        "llama.cpp.install": lambda: program(c, "llama.cpp") is not None,
        "llama.cpp.model": lambda: llama_model(c, home),
        "strata.install": lambda: program(c, "strata") is not None,
        "strata.engine": lambda: any(
            executable(p / "strata") and executable(p / "strata-vision")
            for p in engines.glob("*")
        ),
        "strata.model": lambda: strata_model(home),
    }
    rows = []
    for recipe in recipe_specs():
        entry = recipe.entry
        check = checks[recipe.key]
        try:
            installed = bool(check())
            reason = ""
        except (OSError, ValueError) as ex:
            installed = False
            reason = str(ex)
        rows.append({"entry": entry, "installed": installed, "reason": reason})
    return rows
