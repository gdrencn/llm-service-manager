"""Local model discovery, never starts a service or downloads anything."""

import copy
import hashlib
import json
import re
from pathlib import Path

from .artifacts import embedded_mtp, ollama_manifest
from .availability import available_backends
from .paths import hf_cache, ollama_roots, strata_install


def strata_install_configs(c):
    home = Path(c["runtime"]["home"])
    return sorted((strata_install(home)).glob("strata-*.json"))


def discover(c):
    """Local inventory only. Never starts services or downloads."""
    c = copy.deepcopy(c)
    home = Path(c["runtime"]["home"])
    installed = available_backends(c)
    paths = {m.get("path") or m.get("config_path") for m in c["catalog"]}
    for p in strata_install_configs(c) if "strata" in installed else []:
        if str(p) in paths:
            continue
        try:
            cfg = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if (
            not isinstance(cfg, dict)
            or not isinstance(cfg.get("args"), list)
            or not isinstance(cfg.get("cwd"), str)
        ):
            continue
        ident = "strata-" + hashlib.sha256(str(p).encode()).hexdigest()[:10]
        c["catalog"].append(
            {
                "id": ident,
                "backend": "strata",
                "name": cfg["model_name"]
                if isinstance(cfg.get("model_name"), str)
                else p.stem,
                "config_path": str(p),
                "mtp_supported": "--mtp" in cfg.get("args", []),
            }
        )
        c["model_selection"].setdefault("strata", ident)
    for p in (
        sorted((hf_cache(home)).glob("models--*/snapshots/*/*.gguf"))
        if "llama.cpp" in installed
        else []
    ):
        if str(p) in paths or any(
            x in p.name.lower() for x in ["mmproj", "mtp", "draft"]
        ):
            continue
        if re.search(r"-\d{5}-of-\d{5}", p.name) and "-00001-of-" not in p.name:
            continue
        ident = "gguf-" + hashlib.sha256(str(p).encode()).hexdigest()[:10]
        mm = sorted(p.parent.glob("*mmproj*.gguf"))
        drafts = sorted(
            set(p.parent.glob("*mtp*.gguf")) | set(p.parent.glob("*MTP*.gguf"))
        )
        draft = str(drafts[0]) if len(drafts) == 1 else ""
        c["catalog"].append(
            {
                "id": ident,
                "backend": "llama.cpp",
                "name": p.name,
                "path": str(p),
                "vision_path": str(mm[0]) if len(mm) == 1 else "",
                "mtp_supported": bool(draft) or embedded_mtp(p),
                "mtp_path": draft,
            }
        )
        c["model_selection"].setdefault("llama.cpp", ident)
    # Official Ollama local manifest inventory; supports tags without waking a server.
    roots = ollama_roots(home) if "ollama" in installed else []
    sources = {m.get("source") for m in c["catalog"]}
    for root in roots:
        try:
            files = sorted(p for p in (root / "manifests").rglob("*") if p.is_file())
        except PermissionError:
            continue
        for p in files:
            relative = p.relative_to(root / "manifests").parts
            if len(relative) < 3:
                continue
            source = "/".join(relative[:-1]) + ":" + relative[-1]
            source = source.removeprefix("registry.ollama.ai/library/").removeprefix(
                "registry.ollama.ai/"
            )
            if source in sources or source.startswith(
                ("llmsm-selected:", "llm-config-selected:", "qwen3.8-local:")
            ):
                continue
            ident = "ollama-" + hashlib.sha256(source.encode()).hexdigest()[:10]
            mtp = False
            try:
                manifest = ollama_manifest(root, source)
                for layer in manifest.get("layers", []):
                    if layer.get("mediaType", "").endswith(".model") and re.fullmatch(
                        r"sha256:[0-9a-f]{64}", layer.get("digest", "")
                    ):
                        mtp = embedded_mtp(
                            root / "blobs" / layer["digest"].replace(":", "-")
                        )
                        break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            c["catalog"].append(
                {
                    "id": ident,
                    "backend": "ollama",
                    "name": source,
                    "source": source,
                    "served": "llmsm-selected",
                    "mtp_supported": mtp,
                }
            )
            sources.add(source)
            c["model_selection"].setdefault("ollama", ident)
    return c
