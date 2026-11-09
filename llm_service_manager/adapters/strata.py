"""Render official strata configuration. No I/O except the supplied source reader."""

import json
from pathlib import Path

from ..config import Error, model, value
from ..paths import STRATA_CONFIG, UNITS
from .common import service


def render(c, read):
    b = "strata"
    m = model(c, b)
    home = Path(c["runtime"]["home"])
    out = {}

    def v(key):
        return value(c, key)

    cfg = json.loads(read(m["config_path"]))
    if cfg.get("api_key"):
        raise Error("不自动改写已启用 API Key 的 Strata 配置")
    args = list(cfg["args"])

    def setarg(k, x):
        nonlocal args
        while k in args:
            i = args.index(k)
            del args[
                i : i
                + 1
                + (i + 1 < len(args) and not str(args[i + 1]).startswith("--"))
            ]
        if x is not None:
            args.extend([k, str(x)])

    for k, x in {
        "--max-context": v("context"),
        "--kv": {"q8_0": "int8", "q4_0": "q4_0", "f16": "fp16"}[v("kv")],
        "--vram-reserve-mib": v("reserve_mib"),
        "--ple-io": v("ple_io") if v("ple_io") != "auto" else None,
        "--spec": v("mtp_candidates") if v("mtp") else 0,
    }.items():
        setarg(k, x)
    if not v("mtp"):
        setarg("--mtp", None)
    if "--vision" in args:
        args.remove("--vision")
    if v("vision"):
        args.append("--vision")
    else:
        cfg.pop("vision", None)
    if cfg.get("vision"):
        cfg["vision"]["gpu"] = True
    cfg.update(
        args=args,
        args_written=list(args),
        user_args=[],
        mtp=v("mtp"),
        parallel=v("parallel"),
        drafting="mtp" if v("mtp") else "off",
        host=v("host"),
        port=v("port"),
        idle_unload_s=v("idle_seconds"),
        open_browser=False,
        log=str(home / ".local/state/strata/strata-local.log"),
    )
    out[STRATA_CONFIG] = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    a = [
        str(home / ".local/bin/strata"),
        "serve",
        "--profile",
        STRATA_CONFIG,
        "--host",
        v("host"),
        "--port",
        str(v("port")),
    ]
    out["/etc/systemd/system/" + UNITS[b]] = service(
        c,
        b,
        a,
        extra=("LimitMEMLOCK=infinity\n" if v("lock_memory") else "LimitMEMLOCK=64M\n"),
    )
    return out
