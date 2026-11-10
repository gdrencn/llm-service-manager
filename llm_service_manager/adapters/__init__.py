"""One registry of pure native configuration adapters."""

from pathlib import Path

from ..config import Error, model, validate
from ..config_document import for_backend
from ..paths import BACKENDS
from . import llamacpp, ollama, strata

RENDERERS = {
    "ollama": ollama.render,
    "llama.cpp": llamacpp.render,
    "strata": strata.render,
}


def native(c, read=lambda path: Path(path).read_text(), backends=None):
    selected = BACKENDS if backends is None else backends
    files = {}
    for backend in selected:
        view = for_backend(c, backend)
        validate(view, backend)
        try:
            model(view, backend)
        except Error:
            continue
        files.update(RENDERERS[backend](view, read))
    return files
