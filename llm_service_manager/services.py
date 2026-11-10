"""Official systemd commands and backend APIs. No daemon or inference proxy."""

import re
import time
from pathlib import Path

from .availability import program
from .config import Error, address, model, value
from .execution import Operations
from .model_lifecycle import base_url, ollama_models, wait_router_loaded
from .model_names import public_name
from .network import check_bind
from .paths import (
    ALL_UNITS,
    CONFIG,
    OLLAMA_MODELFILE,
    STRATA_CONFIG,
    UNITS,
)
from .storage import read_config


class Services(Operations):
    def snapshot_runtime(self, config_path, active):
        from .model_transaction import runtime_snapshot

        return runtime_snapshot(config_path, active, self)

    def snapshot_ollama(self, c):
        from .model_transaction import ollama_snapshot

        return ollama_snapshot(c, self)

    def restore_ollama(self, record):
        from .model_transaction import restore_ollama

        restore_ollama(record, self)

    def clear_new_boot_links(self, txn):
        # Remove links while the just-created unit still exists. After restoring
        # absent files systemd would no longer list that unit in available_units.
        for unit in self.available_units():
            if unit not in txn["enabled"]:
                self.run(["systemctl", "disable", unit])

    def check_router(self, c):
        executable = program(c, "llama.cpp")
        if not executable:
            raise Error("llama.cpp 程序不存在")
        help_text = self.run([executable, "serve", "--help"], timeout=30).stdout
        if any(
            flag not in help_text
            for flag in (
                "--models-preset",
                "--models-max",
                "--models-autoload",
            )
        ):
            raise Error("当前 llama.cpp 不支持所需官方 Router 功能，请先按官方方式更新")

    def available_units(self):
        found = []
        for unit in ALL_UNITS:
            r = self.run(
                ["systemctl", "show", unit, "--property=LoadState", "--value"], False
            )
            if r.stdout.strip() == "not-found":
                continue
            if r.returncode:
                raise Error("无法查询 systemd：" + r.stdout.strip())
            if r.stdout.strip():
                found.append(unit)
        return found

    def active(self):
        return [
            u
            for u in self.available_units()
            if self.run(["systemctl", "is-active", u], False).stdout.strip()
            in ("active", "activating", "reloading", "deactivating")
        ]

    def enabled(self):
        return {
            u: self.run(["systemctl", "is-enabled", u], False).stdout.strip()
            for u in self.available_units()
        }

    def boot_target(self, b, enabled=True):
        for u in self.available_units():
            if u != UNITS[b] or not enabled:
                self.run(["systemctl", "disable", u])
        if enabled:
            self.run(["systemctl", "enable", UNITS[b]])

    def restore_boot(self, txn):
        if "enabled" not in txn:
            return
        old = txn["enabled"]
        for u in self.available_units():
            state = old.get(u, "disabled")
            if state in ("enabled", "enabled-runtime"):
                # A failed switch may have replaced a runtime-only enablement
                # with persistent links. Remove both before restoring its scope.
                self.run(["systemctl", "disable", u])
                self.run(
                    [
                        "systemctl",
                        "enable",
                        *(["--runtime"] if state == "enabled-runtime" else []),
                        u,
                    ]
                )
            elif state in ("disabled", "not-found", ""):
                self.run(["systemctl", "disable", u])

    def stop(self):
        units = self.active()
        if units:
            self.run(["systemctl", "stop", *units], timeout=120)
            if self.active():
                raise Error("仍有底座正在运行或停止中，未确认资源释放")

    def check_bind(self, c):
        """After stopping owned services, refuse to act against a foreign listener."""
        host = value(c, "host").strip("[]")
        port = value(c, "port")
        self.event("port-check", host=host, port=port)
        try:
            check_bind(host, port, reuse=True)
        except OSError as ex:
            raise Error(
                "监听地址或端口不可用；不会操作占用它的其它程序：" + str(ex)
            ) from ex

    def prepare_runtime(self, c, files):
        if STRATA_CONFIG in files:
            # mkdir executes as the service user: newly created parents are never root-owned.
            directory = Path(c["runtime"]["home"]) / ".local/state/strata"
            self.run(
                [
                    "sudo",
                    "-H",
                    "-u",
                    c["runtime"]["user"],
                    "--",
                    "mkdir",
                    "-p",
                    str(directory),
                ]
            )

    def wait_ready(self, c, b, unit=None):
        url = base_url(c)
        unit = unit or UNITS[b]
        deadline = time.monotonic() + 900
        endpoint = "/api/tags" if b == "ollama" else "/health"
        while True:
            try:
                result = self.http(url + endpoint)
                if not isinstance(result, dict) or result.get("error"):
                    raise ValueError("服务状态格式无效")
                if b != "ollama" and result.get("status") != "ok":
                    raise ValueError("服务未就绪")
                return url
            except (OSError, ValueError) as ex:
                if time.monotonic() > deadline:
                    raise Error("等待服务就绪超时：" + str(ex)) from ex
                state = self.run(["systemctl", "is-active", unit], False).stdout.strip()
                if state not in ("active", "activating", "reloading"):
                    raise Error("服务已停止或启动失败，请查看日志")
                time.sleep(2)

    def create_ollama_model(self, c, modelfile=OLLAMA_MODELFILE):
        executable = program(c, "ollama")
        if executable is None:
            raise Error("Ollama程序不存在")
        self.run(
            [
                "env",
                "OLLAMA_HOST=" + address(value(c, "host"), value(c, "port"), True),
                executable,
                "create",
                public_name(model(c, "ollama")),
                "-f",
                modelfile,
            ],
            timeout=120,
        )

    def ready(self, c, b, unit=None, modelfile=OLLAMA_MODELFILE, configure=True):
        unit = unit or UNITS[b]
        url = self.wait_ready(c, b, unit)
        m = model(c, b)
        if b == "ollama":
            if configure:
                self.create_ollama_model(c, modelfile)
            tag = public_name(m)
            request = {
                "model": tag,
                "stream": False,
                "keep_alive": str(value(c, "idle_seconds")) + "s"
                if value(c, "idle_seconds")
                else -1,
            }
            result = self.http(url + "/api/generate", request, timeout=900)
            if not isinstance(result, dict) or result.get("error"):
                raise Error("Ollama预加载失败")
            ps = self.http(url + "/api/ps")
            rows = ollama_models(ps)
            loaded = [
                x
                for x in rows
                if x.get("name", "").removesuffix(":latest")
                == tag.removesuffix(":latest")
            ]
            if (
                not loaded
                or type(loaded[0].get("size")) is not int
                or type(loaded[0].get("size_vram")) is not int
                or loaded[0]["size"] <= 0
                or loaded[0]["size_vram"] < loaded[0]["size"]
            ):
                raise Error("未确认模型100% GPU加载，拒绝接受本次切换")
        elif b == "llama.cpp":
            wait_router_loaded(self, url, public_name(m))
            self.check_gpu_offload(unit)

    def check_gpu_offload(self, unit):
        inv = self.run(
            ["systemctl", "show", unit, "--property=InvocationID", "--value"]
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", inv) or int(inv, 16) == 0:
            raise Error("未取得本次服务启动标识，不能用历史日志验证 GPU 加载")
        log = self.run(
            [
                "journalctl",
                "_SYSTEMD_INVOCATION_ID=" + inv,
                "--no-pager",
                "-o",
                "cat",
            ]
        ).stdout
        matches = re.findall(r"offloaded\s+(\d+)/(\d+)\s+layers", log)
        if not matches or any(int(a) != int(z) or int(z) <= 0 for a, z in matches):
            raise Error("日志未确认所有模型层均已加载到GPU")

    def restored(self, txn):
        c = read_config(Path(txn.get("config_path", str(CONFIG))))
        runtime = txn.get("runtime")
        for unit in txn["active"]:
            backend = next(key for key, val in UNITS.items() if val == unit)
            if runtime is None or runtime["backend"] != backend:
                # A pre-existing native service need not have an llmsm model or
                # listener configuration (e.g. fresh Ollama on its default port).
                if (
                    self.run(["systemctl", "is-active", unit]).stdout.strip()
                    != "active"
                ):
                    raise Error("未恢复操作前运行的服务：" + unit)
                continue
            self.ready(c, backend, unit=unit, configure=False)
            if not runtime["loaded"]:
                from .model_lifecycle import release

                release(c, self)
