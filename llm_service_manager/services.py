"""Official systemd commands and backend APIs. No daemon or inference proxy."""

import re
import time
from pathlib import Path

from .availability import program
from .config import Error, address, model, value
from .execution import Operations
from .network import check_bind
from .paths import CONFIG, OLLAMA_MODELFILE, STRATA_CONFIG, UNITS
from .storage import read_config


class Services(Operations):
    def available_units(self):
        found = []
        for unit in UNITS.values():
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

    def wait_ready(self, c, b):
        url = "http://" + address(value(c, "host"), value(c, "port"), True)
        unit = UNITS[b]
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

    def create_ollama_model(self, c):
        executable = program(c, "ollama")
        if executable is None:
            raise Error("Ollama程序不存在")
        self.run(
            [
                "env",
                "OLLAMA_HOST=" + address(value(c, "host"), value(c, "port"), True),
                executable,
                "create",
                model(c, "ollama").get("served", "llmsm-selected"),
                "-f",
                OLLAMA_MODELFILE,
            ],
            timeout=120,
        )

    def ready(self, c, b):
        url = self.wait_ready(c, b)
        unit = UNITS[b]
        m = model(c, b)
        if b == "ollama":
            self.create_ollama_model(c)
            tag = m.get("served", "llmsm-selected")
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
            if (
                not isinstance(ps, dict)
                or not isinstance(ps.get("models"), list)
                or any(
                    not isinstance(x, dict) or not isinstance(x.get("name"), str)
                    for x in ps["models"]
                )
            ):
                raise Error("Ollama模型状态返回格式无效")
            loaded = [
                x
                for x in ps.get("models", [])
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
            inv = self.run(
                ["systemctl", "show", unit, "--property=InvocationID", "--value"]
            ).stdout.strip()
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
            if not matches or any(int(a) != int(z) for a, z in matches):
                raise Error("日志未确认所有模型层均已加载到GPU")

    def restored(self, txn):
        c = read_config(Path(txn.get("config_path", str(CONFIG))))
        for unit in txn["active"]:
            backend = next(key for key, val in UNITS.items() if val == unit)
            try:
                model(c, backend)
            except Error:
                self.wait_ready(c, backend)
            else:
                self.ready(c, backend)
