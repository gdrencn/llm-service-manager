"""Read-only backend capability checks, independent of registered model selections."""

import os
from pathlib import Path

from .artifacts import complete_asset, gguf_set, strata_assets
from .ollama_store import complete_ollama
from .paths import BACKENDS, ollama_roots, strata_engines


def executable(path):
    try:
        return Path(path).is_file() and os.access(path, os.X_OK)
    except (OSError, ValueError, TypeError):
        return False


def program(c, backend):
    home = Path(c["runtime"]["home"])
    candidates = {
        "llama.cpp": [home / ".local/bin/llama"],
        "strata": [home / ".local/bin/strata"],
        "ollama": [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")],
    }.get(backend, [])
    return next((str(p) for p in candidates if executable(p)), None)


def available_backends(c):
    result = []
    for backend in BACKENDS:
        if not program(c, backend):
            continue
        if backend == "strata":
            root = strata_engines(c["runtime"]["home"])
            if not any(executable(p) for p in root.glob("*/strata")):
                continue
        result.append(backend)
    return result


def model_present(c, m):
    try:
        if m["backend"] == "ollama":
            return any(
                complete_ollama(root, m.get("source", ""))
                for root in ollama_roots(c["runtime"]["home"])
            )
        if m["backend"] == "llama.cpp":
            return gguf_set(m.get("path", ""))
        if m["backend"] == "strata":
            import json

            data = json.loads(Path(m.get("config_path", "")).read_text())
            # Optional features can be disabled in the configuration menu; core
            # weights and tokenizer must be complete before offering a model.
            return all(
                complete_asset(p) for p in strata_assets(data, vision=False, mtp=False)
            )
    except (OSError, ValueError, TypeError, IndexError, AttributeError):
        pass
    return False
