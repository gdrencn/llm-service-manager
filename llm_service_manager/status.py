"""Read-only status snapshots; never sends inference or load requests."""

import csv
import io
import subprocess
import time
from pathlib import Path

from .availability import available_backends
from .config import SPECS, Error, address, model, value
from .model_lifecycle import base_url, ollama_models, router_models
from .paths import BACKENDS, existing_unit
from .presentation import formatted, label, render, rich, section, t
from .services import Services as Ops


def collect(c, ops=None):
    ops = ops or Ops(quiet=True)
    result = {"services": {}, "runtime": {}, "errors": [], "gpu": [], "memory": {}}
    installed = available_backends(c)
    for b in BACKENDS:
        unit = existing_unit(b)
        try:
            r = ops.run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "LoadState",
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "UnitFileState",
                    "-p",
                    "MemoryCurrent",
                    "-p",
                    "CPUUsageNSec",
                    "-p",
                    "ActiveEnterTimestampMonotonic",
                ],
                False,
                timeout=5,
            )
            properties = dict(
                line.split("=", 1) for line in r.stdout.splitlines() if "=" in line
            )
            if r.returncode and properties.get("LoadState") != "not-found":
                raise Error(r.stdout.strip() or "无法查询 systemd")
            if b not in installed and properties.get("LoadState") == "not-found":
                continue
            properties["ProgramAvailable"] = b in installed
            result["services"][b] = properties
        except (OSError, TimeoutError, subprocess.TimeoutExpired, Error) as ex:
            result["errors"].append(str(ex))
    active = [
        b for b, p in result["services"].items() if p.get("ActiveState") == "active"
    ]
    # Only query the endpoint when exactly one owned service is running.
    if len(active) == 1:
        b = active[0]
        url = base_url(c)
        endpoint = (
            "/api/ps" if b == "ollama" else "/v1/status" if b == "strata" else "/models"
        )
        try:
            data = ops.http(url + endpoint, timeout=3)
            if not isinstance(data, dict):
                raise ValueError("状态接口返回格式无效")
            if b == "ollama":
                ollama_models(data)
            elif b == "llama.cpp":
                router_models(data)
            if (
                b == "strata"
                and "activity" in data
                and not isinstance(data["activity"], dict)
            ):
                raise ValueError("Strata 活动状态格式无效")
            result["runtime"] = {"backend": b, "response": data}
        except (Error, OSError, ValueError) as ex:
            result["errors"].append(
                t("模型接口暂不可查询：", "Model endpoint unavailable: ") + str(ex)
            )
    try:
        r = ops.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            False,
            timeout=5,
        )
        if r.returncode == 0:
            result["gpu"] = [
                row for row in csv.reader(io.StringIO(r.stdout)) if len(row) == 4
            ]
    except (OSError, TimeoutError, subprocess.TimeoutExpired, Error):
        pass
    try:
        mem = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:"))
        }
        result["memory"] = mem
    except (OSError, ValueError):
        pass
    return result


def gib(n):
    try:
        n = int(n)
        return (
            f"{n / 1024**3:.2f} GiB" if 0 <= n < 2**63 else t("不可用", "Unavailable")
        )
    except (TypeError, ValueError):
        return t("不可用", "Unavailable")


def field(name, val, tone="value"):
    print(render(rich(("  " + name + "：", "key"), (str(val), tone))))


def show(c, snapshot=None):
    s = snapshot if snapshot is not None else collect(c)
    print(
        t(
            "这是只读快照，不会唤醒或加载模型。服务运行不等于模型已驻留内存。",
            "Read-only snapshot; it does not load models. A running service does not necessarily have a resident model.",
        )
    )
    target = c.get("target")
    section(t("当前选择与服务", "Selected model and services"))
    if target:
        b = target["backend"]
        try:
            name = model(c, b, target["model"])["name"]
        except Error:
            name = target["model"]
        field(t("已保存的模型", "Saved model"), b + " / " + name)
    else:
        field(t("已保存的模型", "Saved model"), t("尚未选择", "None"))
    states = {
        "active": t("运行中", "Running"),
        "inactive": t("已停止", "Stopped"),
        "failed": t("启动或运行失败", "Failed"),
        "activating": t("正在启动", "Starting"),
        "deactivating": t("正在停止", "Stopping"),
    }
    for b, p in s["services"].items():
        state = p.get("ActiveState", "unknown")
        field(
            b,
            t("未安装服务", "Service not installed")
            if p.get("LoadState") == "not-found"
            else states.get(state, state),
            "good" if state == "active" else "muted",
        )
        if p.get("ProgramAvailable") is False:
            field(
                t("  程序文件", "  Program files"),
                t(
                    "未找到；以上为已有服务的实际状态",
                    "Missing; the state above is from the existing service",
                ),
                "warn",
            )
        field(
            t("  开机自启（systemd实际状态）", "  Boot enablement (systemd)"),
            p.get("UnitFileState", t("未知", "Unknown")),
        )
        if state == "active":
            field(
                t(
                    "  服务内存（含子进程及记账缓存）",
                    "  Service memory (children and accounted cache)",
                ),
                gib(p.get("MemoryCurrent")),
            )
            try:
                since = int(p["ActiveEnterTimestampMonotonic"]) / 1e6
                field(
                    t("  连续运行时长", "  Service uptime"),
                    f"{max(0, time.monotonic() - since) / 60:.1f} min",
                )
            except (KeyError, ValueError):
                pass
            try:
                cpu = int(p["CPUUsageNSec"])
                if cpu < 2**63:
                    field(
                        t(
                            "  累计 CPU 时间（不是当前占用率）",
                            "  Accumulated CPU time (not utilization)",
                        ),
                        f"{cpu / 1e9:.1f} s",
                    )
            except (KeyError, ValueError):
                pass
    runtime = s["runtime"]
    data = runtime.get("response", {})
    b = runtime.get("backend")
    if b == "ollama":
        models = data.get("models", [])
        field(
            t("模型驻留", "Model residency"),
            t("已加载", "Loaded")
            if models
            else t(
                "未加载；服务空闲等待请求", "Unloaded; service waiting for requests"
            ),
        )
        for m in models:
            field(t("实际加载的模型", "Actually loaded model"), m.get("name", "?"))
            field(
                t("模型占用显存（接口报告）", "Model VRAM (reported by API)"),
                gib(m.get("size_vram")),
            )
            if "context_length" in m:
                field(t("运行上下文", "Runtime context"), m["context_length"])
            if "expires_at" in m:
                field(
                    t("预计空闲卸载时间", "Expected idle unload time"), m["expires_at"]
                )
    elif b == "strata":
        field(
            t("模型驻留", "Model residency"),
            t("已加载", "Loaded")
            if data.get("loaded") is True
            else t("未加载", "Unloaded")
            if data.get("loaded") is False
            else t("未知", "Unknown"),
        )
        field(t("实际模型", "Runtime model"), data.get("model", "?"))
        if "cache_max_tokens" in data:
            field(t("运行上下文", "Runtime context"), data["cache_max_tokens"])
        for k, zh, en in [
            ("requests", "累计请求", "Total requests"),
            ("in_flight", "执行中及排队请求", "Running and queued requests"),
        ]:
            if k in data.get("activity", {}):
                field(t(zh, en), data["activity"][k])
    elif b == "llama.cpp":
        labels = {
            "loaded": t("已加载", "Loaded"),
            "unloaded": t("已卸载；新请求会自动加载", "Unloaded; reloads on request"),
            "sleeping": t("休眠；新请求会自动加载", "Sleeping; reloads on request"),
            "loading": t("加载中", "Loading"),
            "unloading": t("卸载中", "Unloading"),
        }
        try:
            for item in router_models(data):
                state = item["status"]
                field(t("实际模型", "Runtime model"), item["id"])
                field(
                    t("模型驻留", "Model residency"),
                    t("加载失败", "Load failed")
                    if state.get("failed")
                    else labels.get(state["value"], state["value"]),
                )
        except Error as ex:
            field(t("模型驻留", "Model residency"), str(ex), "warn")
    if not runtime:
        field(
            t("模型驻留", "Model residency"),
            t(
                "未取得实时报告；已停止的服务不持有模型",
                "No live report; stopped services do not hold models",
            ),
        )
    section(
        t(
            "已保存的参数（不是全部参数的实时回读）",
            "Saved settings (not a live readback of every parameter)",
        )
    )
    active = [b for b, p in s["services"].items() if p.get("ActiveState") == "active"]
    selected = target["backend"] if target else None
    if active and selected not in active:
        print(
            t(
                "运行服务与保存的目标不一致，请以实际服务和接口报告为准。",
                "Running service differs from saved selection; consult live service/API data.",
            )
        )
    for k in SPECS:
        if selected is None or selected in SPECS[k][2]:
            field(label(k), formatted(value(c, k)))
    field(
        t("接口地址", "Endpoint"),
        "http://" + address(value(c, "host"), value(c, "port")) + "/v1",
        "link",
    )
    section(
        t(
            "整机资源（包括其它程序）",
            "Whole-system resources (includes other programs)",
        )
    )
    mem = s["memory"]
    if mem:
        field(
            t("可用 / 总内存", "Available / total RAM"),
            gib(mem.get("MemAvailable")) + " / " + gib(mem.get("MemTotal")),
        )
    for name, used, total, util in s["gpu"]:
        field(
            name.strip(),
            t("显存 ", "VRAM ")
            + f"{used.strip()} / {total.strip()} MiB · GPU {util.strip()}%",
        )
    if not s["gpu"]:
        print(t("未取得 NVIDIA GPU 统计。", "NVIDIA GPU statistics unavailable."))
    for error in s["errors"]:
        print(render(rich((error, "warn"))))
