"""One registry of pure native configuration adapters."""

from pathlib import Path

from ..config import Error, model, validate
from ..paths import BACKENDS
from . import llamacpp, ollama, strata

RENDERERS = {
    "ollama": ollama.render,
    "llama.cpp": llamacpp.render,
    "strata": strata.render,
}


def native(c, read=lambda path: Path(path).read_text(), backends=None):
    selected = BACKENDS if backends is None else backends
    validate(c, selected[0] if len(selected) == 1 else None)
    files = {}
    for backend in selected:
        try:
            model(c, backend)
        except Error:
            continue
        files.update(RENDERERS[backend](c, read))
    return files
