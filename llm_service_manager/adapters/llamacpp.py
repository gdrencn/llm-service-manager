"""Render official llama.cpp configuration. No I/O except the supplied source reader."""

from pathlib import Path

from ..config import model, value
from .common import service


def render(c, read):
    b = "llama.cpp"
    m = model(c, b)
    home = Path(c["runtime"]["home"])
    out = {}

    def v(key):
        return value(c, key)

    a = [
        str(home / ".local/bin/llama"),
        "serve",
        "--model",
        m["path"],
        "--alias",
        m["id"],
        "--host",
        v("host"),
        "--port",
        str(v("port")),
        "--ctx-size",
        str(v("context")),
        "--parallel",
        str(v("parallel")),
        "--cache-type-k",
        v("kv"),
        "--cache-type-v",
        v("kv"),
        "--flash-attn",
        "on" if v("flash_attention") else "off",
        "--fit",
        "on" if v("fit") else "off",
        "--fit-target",
        str(v("reserve_mib")),
        "--sleep-idle-seconds",
        str(v("idle_seconds") or -1),
        "--gpu-layers",
        "all",
        "--kv-offload",
        # Official loading diagnostics are required to verify full GPU offload.
        "--log-verbosity",
        "4",
    ]
    if v("vision"):
        a += ["--mmproj", m.get("vision_path", ""), "--mmproj-offload"]
    if v("mtp"):
        a += [
            "--spec-type",
            "draft-mtp",
            "--spec-draft-n-max",
            str(v("mtp_candidates")),
            "--spec-draft-type-k",
            v("mtp_kv"),
            "--spec-draft-type-v",
            v("mtp_kv"),
            "--spec-draft-ngl",
            "all",
        ]
        if m.get("mtp_path"):
            a += ["--spec-draft-model", m["mtp_path"]]
    out["/etc/systemd/system/llm-llama.service"] = service(c, b, a)
    return out
