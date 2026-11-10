"""Audited command/file/HTTP I/O, used only while the tool runs."""

import json
import subprocess
import time
import urllib.request
from concurrent.futures import CancelledError
from pathlib import Path

from .config import Error
from .paths import STATE
from .storage import atomic


def inspection_command(args):
    """Keep routine checks in the audit log, without flooding progress output."""
    argv = [str(arg) for arg in args]
    if not argv:
        return False
    name = Path(argv[0]).name
    return (
        (
            name == "systemctl"
            and len(argv) > 1
            and argv[1] in ("show", "is-active", "is-enabled")
        )
        or name in ("journalctl", "systemd-analyze")
        or "--help" in argv
    )


def report_outcome(ops, kind, **data):
    """A failed final log write must not reverse a completed action or hide its error."""
    try:
        ops.event(kind, **data)
    except OSError as ex:
        import sys

        print(f"操作结果 {kind}；审计记录写入失败：{ex}；{data}", file=sys.stderr)


def cancellable_run(args, cancel, timeout):
    """Bounded read-only subprocess work; terminate our child when its page is left."""
    if cancel.is_set():
        raise CancelledError
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as proc:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel.is_set():
                    raise CancelledError
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    output, _ = proc.communicate(timeout=min(0.1, remaining))
                    return subprocess.CompletedProcess(args, proc.returncode, output)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


class Operations:
    def __init__(self, state=STATE, quiet=False, cancel=None):
        self.state = Path(state)
        self.quiet = quiet
        self.cancel = cancel

    def event(self, kind, **data):
        self.state.mkdir(parents=True, exist_ok=True)
        row = {"time": time.time(), "kind": kind, **data}
        with (self.state / "audit.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if not self.quiet:
            from .presentation import render, rich, t

            if kind == "progress":
                print(data.get("message", ""), flush=True)
            elif kind == "command" and not inspection_command(data["argv"]):
                import shlex

                print(
                    render(rich(("$ " + shlex.join(map(str, data["argv"])), "muted"))),
                    flush=True,
                )
            elif kind == "warning":
                print(render(rich((data.get("message", ""), "warn"))), flush=True)
            elif kind in (
                "transaction-start",
                "transaction-committed",
                "rollback-completed",
                "rollback-failed",
            ):
                names = {
                    "transaction-start": t(
                        "正在应用配置并启动模型…",
                        "Applying configuration and starting model…",
                    ),
                    "transaction-committed": t(
                        "模型已启动，配置已保存。",
                        "Model started; configuration saved.",
                    ),
                    "rollback-completed": t(
                        "已恢复之前的配置和服务。",
                        "Previous configuration and services restored.",
                    ),
                    "rollback-failed": t(
                        "恢复失败，请运行 llmsm recover。",
                        "Recovery failed; run llmsm recover.",
                    ),
                }
                print(names[kind], flush=True)
            elif kind == "write" and not str(data.get("path", "")).startswith(
                "/tmp/llm-unit-check-"
            ):
                print(t("写入：", "Write: ") + str(data.get("path", "")), flush=True)

    def run(self, args, check=True, timeout=120):
        self.event("command", argv=args)
        try:
            r = (
                cancellable_run(args, self.cancel, timeout)
                if self.cancel is not None
                else subprocess.run(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                )
            )
        except CancelledError:
            self.event("command-cancelled", argv=args)
            raise
        except (OSError, subprocess.TimeoutExpired) as ex:
            self.event("command-failed", error=str(ex))
            raise Error("命令无法完成：" + str(ex)) from ex
        self.event("result", exit=r.returncode, output=r.stdout)
        if check and r.returncode:
            raise Error(r.stdout.strip() or "命令失败：" + str(args))
        return r

    def http(self, url, body=None, timeout=15):
        self.event("http", url=url, body=body)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.load(r)
        if not isinstance(result, dict):
            raise ValueError("接口返回的 JSON 不是对象")
        self.event("http-result", response=result)
        return result

    def write(self, path, text):
        self.event("write", path=str(path), content=text)
        atomic(path, text)
