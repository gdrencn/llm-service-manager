"""Official llama.cpp Router and preset; no tool-owned serving process."""

from pathlib import Path

from ..config import Error, model, value
from ..model_names import public_name
from ..paths import LLAMA_PRESET, UNITS
from .common import service


def ini_value(value):
    text = str(value)
    # The official parser has no quoting/escaping for these delimiters.
    if not text or text != text.strip() or any(c in text for c in "\r\n;#\x00"):
        raise Error("llama.cpp 官方 preset 无法表示这个值：" + repr(text))
    return text


def render(c, read):
    b = "llama.cpp"
    m = model(c, b)
    home = Path(c["runtime"]["home"])

    def v(key):
        return value(c, key)

    name = ini_value(public_name(m))
    if any(ch in name for ch in "[]") or name == "*":
        raise Error("模型名称不能作为 llama.cpp preset 节名")
    params = {
        "model": m["path"],
        "ctx-size": v("context") * v("parallel"),
        "parallel": v("parallel"),
        "cache-type-k": v("kv"),
        "cache-type-v": v("kv"),
        "flash-attn": "on" if v("flash_attention") else "off",
        "fit": "on" if v("fit") else "off",
        "fit-target": v("reserve_mib"),
        "sleep-idle-seconds": v("idle_seconds") or -1,
        "gpu-layers": "all",
        "kv-offload": "true",
        "log-verbosity": 4,
        "load-on-startup": "true",
        "spec-type": "draft-mtp" if v("mtp") else "none",
    }
    if v("vision"):
        params["mmproj-auto"] = "true"
        if m.get("vision_path"):
            params["mmproj"] = m["vision_path"]
        params["mmproj-offload"] = "true"
    else:
        params["no-mmproj"] = "true"
    if v("mtp"):
        params.update(
            {
                "spec-draft-n-max": v("mtp_candidates"),
                "spec-draft-type-k": v("mtp_kv"),
                "spec-draft-type-v": v("mtp_kv"),
                "spec-draft-ngl": "all",
            }
        )
        if m.get("mtp_path"):
            params["spec-draft-model"] = m["mtp_path"]
    preset = "version = 1\n\n[" + name + "]\n"
    preset += "".join(k + " = " + ini_value(x) + "\n" for k, x in params.items())
    args = [
        str(home / ".local/bin/llama"),
        "serve",
        "--host",
        v("host"),
        "--port",
        str(v("port")),
        "--models-preset",
        LLAMA_PRESET,
        "--models-max",
        "1",
        "--models-autoload",
    ]
    return {
        LLAMA_PRESET: preset,
        "/etc/systemd/system/" + UNITS[b]: service(
            c,
            b,
            args,
            extra=(
                "CacheDirectory=llama-server-local\n"
                "Environment=LLAMA_CACHE=/var/cache/llama-server-local\n"
            ),
        ),
    }
