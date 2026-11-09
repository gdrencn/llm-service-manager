"""Bounded, read-only model artifact inspection shared by every inventory."""

import json
import os
import re
import struct
from pathlib import Path


def nonempty(path):
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 0
    except (OSError, ValueError, TypeError):
        return False


def gguf(path):
    try:
        with Path(path).open("rb") as f:
            return f.read(4) == b"GGUF" and Path(path).stat().st_size > 24
    except (OSError, ValueError, TypeError):
        return False


def embedded_mtp(path):
    """Inspect bounded GGUF metadata only; never reads weight tensors."""
    try:
        with Path(path).open("rb") as f:
            total = os.fstat(f.fileno()).st_size

            def number(fmt):
                return struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))[0]

            def skip(n):
                if n < 0 or f.tell() + n > total:
                    raise ValueError("truncated GGUF")
                f.seek(n, 1)

            def string():
                n = number("Q")
                if n > 1048576:
                    raise ValueError("oversized metadata key")
                return f.read(n).decode("utf-8")

            sizes = {
                0: 1,
                1: 1,
                2: 2,
                3: 2,
                4: 4,
                5: 4,
                6: 4,
                7: 1,
                10: 8,
                11: 8,
                12: 8,
            }

            def skip_value(kind, depth=0):
                if depth > 2:
                    raise ValueError("nested metadata")
                if kind in sizes:
                    skip(sizes[kind])
                elif kind == 8:
                    skip(number("Q"))
                elif kind == 9:
                    child = number("I")
                    n = number("Q")
                    if child in sizes:
                        skip(n * sizes[child])
                    else:
                        if n > 2000000:
                            raise ValueError("oversized array")
                        for _ in range(n):
                            skip_value(child, depth + 1)
                else:
                    raise ValueError("metadata type")

            if f.read(4) != b"GGUF" or number("I") not in (2, 3):
                return False
            number("Q")
            count = number("Q")
            if count > 100000:
                return False
            for _ in range(count):
                key = string()
                kind = number("I")
                if key.endswith("nextn_predict_layers") and kind in (4, 5, 10, 11):
                    return number({4: "I", 5: "i", 10: "Q", 11: "q"}[kind]) > 0
                skip_value(kind)
    except (OSError, ValueError, UnicodeError, struct.error):
        pass
    return False


def gguf_set(path):
    p = Path(path)
    if not gguf(p):
        return False
    m = re.search(r"-(\d{5})-of-(\d{5})", p.name)
    return not m or (
        m[1] == "00001"
        and 0 < int(m[2]) <= 10000
        and all(
            gguf(p.with_name(p.name[: m.start(1)] + f"{i:05d}" + p.name[m.end(1) :]))
            for i in range(1, int(m[2]) + 1)
        )
    )


def manifest_relative(name):
    # A registry may contain a port; only a colon after the final slash denotes a tag.
    slash = name.rfind("/")
    colon = name.rfind(":")
    base, tag = (name[:colon], name[colon + 1 :]) if colon > slash else (name, "latest")
    parts = base.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library", *parts]
    elif not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = ["registry.ollama.ai", *parts]
    return Path(*parts) / tag


def ollama_manifest(root, source):
    """Validate metadata and every referenced blob without loading or hashing weights."""
    root = Path(root)
    manifest = json.loads((root / "manifests" / manifest_relative(source)).read_text())
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("layers"), list)
        or not manifest["layers"]
    ):
        raise ValueError("manifest 缺少模型层")
    for layer in [manifest.get("config"), *manifest["layers"]]:
        if not isinstance(layer, dict):
            raise ValueError("manifest 层格式无效")
        digest, size = layer.get("digest"), layer.get("size")
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", digest
        ):
            raise ValueError("manifest digest 无效")
        if type(size) is not int or size <= 0:
            raise ValueError("manifest blob 大小无效")
        blob = root / "blobs" / digest.replace(":", "-")
        if not blob.is_file() or blob.stat().st_size != size:
            raise ValueError("模型 blob 缺失或大小不符：" + str(blob))
    return manifest


def complete_ollama(root, source):
    try:
        ollama_manifest(root, source)
        return True
    except (OSError, ValueError, TypeError):
        return False


def strata_assets(cfg, vision=True, mtp=True):
    """List required model files once, shared by preflight and install inventory."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("args"), list):
        raise ValueError("Strata 配置需要 args 列表")
    args = cfg["args"]
    if any(not isinstance(x, str) or any(ord(c) < 32 for c in x) for x in args):
        raise ValueError("Strata 参数必须是不含控制字符的字符串")

    def argument(key):
        if key not in args:
            raise ValueError("Strata 缺少参数：" + key)
        i = args.index(key)
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            raise ValueError("Strata 参数缺少值：" + key)
        p = Path(args[i + 1])
        if not p.is_absolute():
            raise ValueError("Strata 模型路径必须是绝对路径：" + key)
        return p

    paths = []
    if not any(k in args for k in ("--native", "--native-dense-gguf")):
        raise ValueError("Strata 缺少 dense 模型")
    for key in ("--native", "--native-dense-gguf", "--ple-gguf", "--embd-gguf"):
        if key in args:
            paths.append(argument(key))
    pack = argument("--pack")
    paths.extend(pack / name for name in ("dense.bin", "experts.bin", "index.txt"))
    tokenizer = cfg.get("tokenizer")
    if not isinstance(tokenizer, str) or not Path(tokenizer).is_absolute():
        raise ValueError("Strata 缺少有效的 tokenizer 路径")
    # Official serve/server.py reads these three files before loading the engine.
    paths.extend(
        Path(tokenizer) / name
        for name in ("vocab.json", "merges.txt", "token_type.json")
    )
    if vision:
        vi = cfg.get("vision")
        if (
            not isinstance(vi, dict)
            or not isinstance(vi.get("mmproj"), str)
            or not Path(vi["mmproj"]).is_absolute()
        ):
            raise ValueError("Strata 缺少有效的视觉模型路径")
        paths.append(Path(vi["mmproj"]))
    if mtp:
        draft = argument("--mtp")
        paths.extend(
            draft / name
            for name in ("dense.bin", "dense.txt", "experts.bin", "draft_vocab.bin")
        ) if draft.is_dir() else paths.append(draft)
    return tuple(dict.fromkeys(paths))


def complete_asset(path):
    return gguf_set(path) if Path(path).suffix.lower() == ".gguf" else nonempty(path)
