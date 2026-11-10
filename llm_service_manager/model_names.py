"""Public model names come from installed artifacts, never manager identifiers."""

import re
from pathlib import Path


def public_name(entry):
    backend = entry["backend"]
    if backend == "llama.cpp":
        name = Path(entry["path"]).name
        if name.lower().endswith(".gguf"):
            name = name[:-5]
        # A multipart artifact is one model, not a model named after its first shard.
        return re.sub(r"-\d{5}-of-\d{5}$", "", name)
    if backend == "ollama":
        source = entry["source"]
        return source if ":" in source.rsplit("/", 1)[-1] else source + ":latest"
    return entry["name"]


def normalize_names(config):
    """Derive public names from artifacts without changing internal identities."""
    for entry in config.get("catalog", []):
        if not isinstance(entry, dict):
            continue
        backend = entry.get("backend")
        if not isinstance(backend, str):
            # Schema validation reports malformed entries; normalization must not crash first.
            continue
        field = {"llama.cpp": "path", "ollama": "source"}.get(backend)
        if field and isinstance(entry.get(field), str) and entry[field]:
            entry["name"] = public_name(entry)
    return config
