"""Native model state and unload APIs; services stay online, no background worker."""

import time
from urllib.error import HTTPError

from .config import Error, address, value
from .execution import report_outcome
from .paths import UNITS


def base_url(c):
    return "http://" + address(value(c, "host"), value(c, "port"), True)


def router_models(data):
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(
        not isinstance(x, dict)
        or not isinstance(x.get("id"), str)
        or not isinstance(x.get("status"), dict)
        or not isinstance(x["status"].get("value"), str)
        for x in rows
    ):
        raise Error(
            "未取得官方 llama.cpp Router 模型状态；当前服务必须使用官方 Router 模式"
        )
    return rows


def router_model(data, name):
    rows = [x for x in router_models(data) if x["id"] == name]
    if len(rows) != 1:
        raise Error("Router 中未找到唯一的目标模型：" + name)
    return rows[0]


def wait_router_loaded(ops, url, name, timeout=900):
    deadline = time.monotonic() + timeout
    while True:
        item = router_model(ops.http(url + "/models"), name)
        state = item["status"]
        if state.get("failed") or state["value"] == "failed":
            raise Error("llama.cpp 模型加载失败：" + str(state))
        if state["value"] == "loaded":
            return
        if time.monotonic() >= deadline:
            raise Error("等待 Router 加载模型超时：" + state["value"])
        time.sleep(1)


def ollama_models(data):
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(
        not isinstance(x, dict) or not isinstance(x.get("name"), str) for x in rows
    ):
        raise Error("Ollama 模型状态返回格式无效")
    return rows


def unloaded(ops, url, backend):
    if backend == "ollama":
        return not ollama_models(ops.http(url + "/api/ps"))
    if backend == "llama.cpp":
        return all(
            x["status"]["value"] == "unloaded"
            for x in router_models(ops.http(url + "/models"))
        )
    data = ops.http(url + "/v1/status")
    if type(data.get("loaded")) is not bool:
        raise Error("Strata 未报告模型加载状态")
    return not data["loaded"]


def release(c, ops, timeout=60):
    active = ops.active()
    if not active:
        return
    target = c.get("target") or {}
    backend = target.get("backend")
    if backend not in UNITS or active != [UNITS[backend]]:
        raise Error("运行服务与选定底座不一致，请先检查服务状态")
    url = base_url(c)
    try:
        if backend == "ollama":
            for item in ollama_models(ops.http(url + "/api/ps")):
                ops.http(
                    url + "/api/generate",
                    {
                        "model": item["name"],
                        "keep_alive": 0,
                        "stream": False,
                    },
                    timeout=timeout,
                )
        elif backend == "llama.cpp":
            rows = router_models(ops.http(url + "/models"))
            # Refuse unknown/transition states rather than reporting a false success.
            for item in rows:
                state = item["status"]["value"]
                if state not in ("loaded", "sleeping", "unloaded"):
                    raise Error("模型状态为 " + state + "，请稍后释放资源")
            for item in rows:
                if item["status"]["value"] != "unloaded":
                    result = ops.http(
                        url + "/models/unload", {"model": item["id"]}, timeout=timeout
                    )
                    if result.get("success") is not True:
                        raise Error("Router 未确认卸载请求成功")
        elif not unloaded(ops, url, backend):
            ops.http(url + "/unload", {}, timeout=timeout)
        deadline = time.monotonic() + timeout
        while not unloaded(ops, url, backend):
            if time.monotonic() >= deadline:
                raise Error("未确认模型资源已释放；可能有新请求重新加载了模型")
            time.sleep(0.5)
        if ops.active() != active:
            raise Error("模型服务状态发生变化，未确认服务保持在线")
        report_outcome(ops, "models-released", backend=backend)
    except HTTPError as ex:
        if ex.code == 409:
            raise Error("模型正在处理请求，尚未释放资源，请在任务结束后重试") from ex
        raise
