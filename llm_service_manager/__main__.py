import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from .commands import commands
from .config import BACKENDS, SPECS, Error, model, parse_setting
from .config_document import for_backend
from .discovery import discover
from .manager import recover, release, stop, switch
from .preflight import preflight
from .presentation import VERSION, boundary, load_language, t
from .status import collect as collect_status
from .status import show as show_status
from .storage import read_config, read_snapshot


def main():
    load_language()
    p = argparse.ArgumentParser(
        description=t(
            "本地模型服务配置与管理", "Local model service configuration and management"
        )
    )
    p.add_argument("--version", action="version", version="llmsm " + VERSION)
    sub = p.add_subparsers(dest="cmd")
    for name in ["models", "recover", "menu", "tui"]:
        sub.add_parser(name)
    sub.add_parser(
        "release", help=t("卸载模型并保留服务", "Unload models, keep service online")
    )
    sub.add_parser("stop", help=t("停止整个底座服务", "Stop backend services"))
    lan = sub.add_parser("lan")
    lan_flags = lan.add_mutually_exclusive_group()
    lan_flags.add_argument("--json", action="store_true")
    lan_flags.add_argument("--configure-linux", action="store_true")
    lan_flags.add_argument("--setup-server", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    cmd = sub.add_parser("commands")
    cmd.add_argument("--backend", choices=(*BACKENDS, "all"), default="all")
    cmd.add_argument("--ollama-model", default="qwen3.8")
    cmd.add_argument("--hf-model", default="unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL")
    for name in ["preview", "switch"]:
        s = sub.add_parser(name)
        s.add_argument("backend", choices=BACKENDS)
        s.add_argument("--model")
        s.add_argument("--set", action="append", default=[])
        if name == "switch":
            s.add_argument("--yes", action="store_true")
    args = p.parse_args()
    if args.cmd == "commands":
        print(commands(args.backend, args.ollama_model, args.hf_model))
        return
    if (args.cmd or "tui") in (
        "menu",
        "tui",
        "switch",
        "stop",
        "release",
        "recover",
        "status",
        "models",
        "preview",
        "lan",
    ) and os.geteuid() != 0:
        # Native sudo owns terminal/password; never capture credentials or add sudoers.
        import shutil

        launcher = shutil.which("llmsm")
        if not launcher:
            raise Error("找不到 llmsm 入口；请使用安装后的 llmsm 命令")
        os.execvp("sudo", ["sudo", "--", str(Path(launcher).resolve()), *sys.argv[1:]])
    if args.cmd in (None, "menu", "tui"):
        from .tui import main as tui

        if not sys.stdin.isatty():
            raise Error("交互菜单需要终端；可使用 models / preview / commands")
        tui()
        return
    if args.cmd == "recover":
        recover()
        return
    if args.cmd == "release":
        release()
        return
    if args.cmd == "stop":
        stop()
        return
    if args.cmd == "lan":
        from .lan import plain, probe

        c = read_config()
        if args.setup_server:
            from .server_setup import complete

            complete()
            return
        if args.configure_linux:
            from .config import value
            from .linux_lan import configure
            from .storage import locked

            with locked():
                configure(value(read_config(), "port"))
            return
        snapshot = probe(c)
        print(
            json.dumps(snapshot, ensure_ascii=False, indent=2)
            if args.json
            else plain(c, snapshot)
        )
        return
    if args.cmd == "status":
        c = read_config()
        if args.json:
            print(json.dumps(collect_status(c), ensure_ascii=False, indent=2))
        else:
            boundary(t("当前模型和服务状态", "Current model and service status"))
            show_status(c)
        return
    c, rev = read_snapshot()
    c = discover(c)
    if args.cmd == "models":
        print(json.dumps(c["catalog"], ensure_ascii=False, indent=2))
        return
    c = for_backend(c, args.backend)
    if args.model:
        c["model_selection"][args.backend] = args.model
    for pair in args.set:
        key, sep, raw = pair.partition("=")
        if not sep or key not in SPECS:
            raise Error("未知设置：" + pair)
        if args.backend not in SPECS[key][2]:
            raise Error("该设置不适用于当前底座：" + key)
        c["settings"][key]["value"] = parse_setting(key, raw)
        if key == "port":
            c.pop("initial_port", None)
    errors, notes = preflight(c, args.backend)
    if args.cmd == "preview":
        print(
            json.dumps(
                {
                    "backend": args.backend,
                    "model": model(c, args.backend),
                    "settings": c["settings"],
                    "errors": errors,
                    "warnings": notes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if errors:
            raise SystemExit(2)
        return
    if errors:
        raise Error("\n".join(errors))
    if not args.yes:
        print(
            json.dumps(
                {
                    "target": model(c, args.backend),
                    "settings": c["settings"],
                    "warnings": notes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if input("应用并切换？输入 yes：") != "yes":
            return
    switch(c, args.backend, rev)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def entry():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("操作已取消", file=sys.stderr)
        sys.exit(130)
    except (Error, OSError, ValueError, subprocess.SubprocessError) as ex:
        print("错误：" + str(ex), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    entry()
