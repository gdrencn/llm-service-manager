"""Read official legacy/v2 Ollama storage without starting or changing the server.

Layout and runner order follow upstream manifest/paths.go and manifest/manifest.go.
"""

import json
import re
from pathlib import Path

PUBLIC_HOSTS = ("registry.ollama.ai", "ollama.com")


def manifest_relative(name):
    slash, colon = name.rfind("/"), name.rfind(":")
    base, tag = (name[:colon], name[colon + 1 :]) if colon > slash else (name, "latest")
    parts = base.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library", *parts]
    elif not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = ["registry.ollama.ai", *parts]
    if len(parts) != 3 or any(p in ("", ".", "..") for p in [*parts, tag]):
        raise ValueError("Ollama 模型名称无效")
    return Path(*parts) / tag


def manifest_path(root, source):
    relative = manifest_relative(source)
    host, *tail = relative.parts
    canonical = "ollama.com" if host.lower() in PUBLIC_HOSTS else host
    candidates = [
        Path(root) / "manifests-v2" / canonical.replace(":", "%3A") / Path(*tail)
    ]
    hosts = [host]
    if host.lower() in PUBLIC_HOSTS:
        hosts.extend(h for h in PUBLIC_HOSTS if h != host)
    candidates.extend(Path(root) / "manifests" / h / Path(*tail) for h in hosts)
    return next((p for p in candidates if p.is_file()), candidates[0])


def sources(root):
    found = set()
    for directory in ("manifests-v2", "manifests"):
        for p in (Path(root) / directory).glob("*/*/*/*"):
            if not p.is_file():
                continue
            host, namespace, name, tag = p.relative_to(Path(root) / directory).parts
            host = host.replace("%3A", ":").replace("%3a", ":")
            prefix = "" if host.lower() in PUBLIC_HOSTS else host + "/"
            if prefix or namespace != "library":
                prefix += namespace + "/"
            found.add(prefix + name + ":" + tag)
    return sorted(found)


def read_metadata(root, path):
    # v2 uses symlinks to small manifest blobs. Do not follow them outside the store.
    path = Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("manifest 指向模型目录之外")
    with path.open("rb") as f:
        data = f.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("manifest 元数据过大")
    return json.loads(data)


def blob_path(root, layer, check_size=True):
    if not isinstance(layer, dict):
        raise ValueError("manifest 层格式无效")
    digest, size = layer.get("digest"), layer.get("size")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("manifest digest 无效")
    if check_size and (type(size) is not int or size <= 0):
        raise ValueError("manifest blob 大小无效")
    blob = Path(root) / "blobs" / digest.replace(":", "-")
    if not blob.is_file():
        raise FileNotFoundError("模型 blob 缺失：" + str(blob))
    if check_size and blob.stat().st_size != size:
        raise ValueError("模型 blob 缺失或大小不符：" + str(blob))
    return blob


def ollama_manifest(root, source):
    """Validate the locally selected manifest and layer sizes; never read weights."""
    manifest = read_metadata(root, manifest_path(root, source))
    if isinstance(manifest, dict) and "manifests" in manifest:
        children = manifest["manifests"]
        if not isinstance(children, list):
            raise ValueError("manifest list 格式无效")
        selected = None
        for runner in ("llamacpp", "ggml", "mlx"):
            child = next(
                (
                    c
                    for c in children
                    if isinstance(c, dict) and c.get("runner") == runner
                ),
                None,
            )
            if child is None:
                continue
            if "layers" not in child:
                try:
                    child = read_metadata(
                        root, blob_path(root, child, check_size=False)
                    )
                except FileNotFoundError:
                    continue
            selected = child
            break
        manifest = selected
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("layers"), list)
        or not manifest["layers"]
    ):
        raise ValueError("manifest 缺少可用模型层")
    for layer in [manifest.get("config"), *manifest["layers"]]:
        blob_path(root, layer)
    return manifest


def complete_ollama(root, source):
    try:
        ollama_manifest(root, source)
        return True
    except (OSError, ValueError, TypeError):
        return False
