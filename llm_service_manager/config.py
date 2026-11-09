"""Shared settings, validation and parsing. No per-model overrides."""

import copy
import os
import pwd
import re
from pathlib import Path

from .paths import BACKENDS

# label, default, applicable backends, input kind
SPECS = {
    "host": ("监听地址", "0.0.0.0", BACKENDS, "text"),
    "port": ("端口", 55555, BACKENDS, "int"),
    "context": ("上下文 tokens（共享）", 131072, BACKENDS, "int"),
    "kv": ("KV 精度", "q8_0", BACKENDS, ("q8_0", "q4_0", "f16")),
    "parallel": ("并发", 1, BACKENDS, "int"),
    "idle_seconds": ("空闲卸载秒数（0=不卸载）", 300, BACKENDS, "int"),
    "reserve_mib": ("显存预留 MiB", 0, BACKENDS, "int"),
    "vision": ("GPU 视觉", True, BACKENDS, "bool"),
    "mtp": ("MTP", True, BACKENDS, "bool"),
    "mtp_candidates": ("MTP 草稿长度", 4, BACKENDS, "int"),
    "mtp_kv": ("MTP KV", "q8_0", ("llama.cpp",), ("q8_0", "q4_0", "f16")),
    "flash_attention": ("Flash Attention", True, BACKENDS[:2], "bool"),
    "fit": ("自动适配 fit", False, ("llama.cpp",), "bool"),
    "gpu_only": ("强制 100% GPU", True, BACKENDS[:2], "bool"),
    "ple_io": ("PLE 内存策略", "ram", ("strata",), ("ram", "auto")),
    "lock_memory": ("允许锁定 PLE 内存", True, ("strata",), "bool"),
    "autostart": ("开机启动选定底座", True, BACKENDS, "bool"),
}


class Error(Exception):
    pass


def value(c, k):
    return c["settings"][k]["value"]


def normalized(raw, home=None, user=None):
    if not isinstance(raw, dict):
        raise Error("配置必须是 JSON 对象")
    try:
        account = pwd.getpwnam(
            user or os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
        )
    except KeyError:
        raise Error("找不到服务用户")
    home = home or account.pw_dir
    user = user or account.pw_name
    for key, kind in [
        ("settings", dict),
        ("runtime", dict),
        ("catalog", list),
        ("model_selection", dict),
        ("models", dict),
    ]:
        if key in raw and not isinstance(raw[key], kind):
            raise Error("配置字段类型错误：" + key)
    if any(not isinstance(v, dict) for v in raw.get("settings", {}).values()):
        raise Error("配置参数必须包含 value")
    if any(not isinstance(v, dict) for v in raw.get("catalog", [])):
        raise Error("模型目录格式错误")
    if raw.get("schema", 2) not in (1, 2):
        raise Error("不支持的配置版本")
    if set(raw.get("settings", {})) - set(SPECS):
        raise Error("配置包含未知参数")
    c = copy.deepcopy(raw)
    c["schema"] = 2
    c.setdefault("runtime", {"home": home, "user": user})
    c.setdefault("settings", {})
    for k, (_, default, applies, _) in SPECS.items():
        c["settings"][k] = {
            "value": c["settings"].get(k, {}).get("value", default),
            "applies_to": list(applies),
        }
    c.setdefault("catalog", [])
    c.setdefault("model_selection", {})
    for b, m in c.pop("models", {}).items():
        if b not in BACKENDS:
            continue
        if not isinstance(m, dict):
            raise Error("旧模型配置必须是对象")
        if any(key in m and not isinstance(m[key], str) for key in ("source", "path")):
            raise Error("旧模型路径和名称必须是字符串")
        if "mtp_supported" in m and type(m["mtp_supported"]) is not bool:
            raise Error("模型 MTP 支持标记必须是布尔值")
        ident = "initial-" + b.replace(".", "")
        if not any(x.get("id") == ident for x in c["catalog"]):
            c["catalog"].append(
                dict(
                    m,
                    id=ident,
                    backend=b,
                    name=m.get("source") or Path(m.get("path", b)).name,
                    mtp_supported=bool(m.get("mtp_supported", False)),
                )
            )
        c["model_selection"].setdefault(b, ident)
    c.pop("notes", None)
    c.setdefault("target", None)
    from .model_names import normalize_names

    return normalize_names(c)


def model(c, b, ident=None):
    ident = ident or c["model_selection"].get(b)
    for m in c["catalog"]:
        if m["id"] == ident and m["backend"] == b:
            return m
    raise Error("请选择已登记的模型：" + b)


def validate(c, backend=None):
    for k, (_, _, applies, kind) in SPECS.items():
        v = value(c, k)
        if c["settings"][k]["applies_to"] != list(applies):
            raise Error("生效对象由适配器定义，不能任意修改：" + k)
        if kind == "int" and (type(v) is not int or v < 0):
            raise Error(k + " 必须是非负整数")
        if kind == "bool" and type(v) is not bool:
            raise Error(k + " 必须是布尔值")
        if isinstance(kind, tuple) and v not in kind:
            raise Error(k + " 不支持该值")
    if not 1 <= value(c, "port") <= 65535:
        raise Error("端口范围为 1–65535")
    if value(c, "context") < 512:
        raise Error("上下文至少为512")
    if not 1 <= value(c, "parallel") <= 8:
        raise Error("并发范围为1–8")
    if not 1 <= value(c, "mtp_candidates") <= 16:
        raise Error("MTP草稿长度范围为1–16")
    if not isinstance(value(c, "host"), str) or not re.fullmatch(
        r"[A-Za-z0-9_.:\[\]-]+", value(c, "host")
    ):
        raise Error("监听地址含无效字符")
    if not value(c, "gpu_only"):
        raise Error("当前部署要求 llama.cpp/Ollama 100% GPU，不允许关闭")
    if (
        backend in BACKENDS[:2]
        and value(c, "kv") != "f16"
        and not value(c, "flash_attention")
    ):
        raise Error("量化KV需要启用 Flash Attention")
    target = c.get("target")
    if target is not None and (
        not isinstance(target, dict)
        or target.get("backend") not in BACKENDS
        or not isinstance(target.get("model"), str)
    ):
        raise Error("当前模型选择格式无效")
    if any(
        b not in BACKENDS or not isinstance(m, str)
        for b, m in c["model_selection"].items()
    ):
        raise Error("底座模型选择格式无效")
    ids = set()
    for m in c["catalog"]:
        if not isinstance(m, dict) or any(
            not isinstance(m.get(k), str) for k in ("id", "backend", "name")
        ):
            raise Error("模型目录项缺少 id/backend/name")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", m["id"]) or m["id"] in ids:
            raise Error("模型ID无效或重复")
        ids.add(m["id"])
        if m["backend"] not in BACKENDS:
            raise Error("未知底座")
        if "mtp_supported" in m and type(m["mtp_supported"]) is not bool:
            raise Error("模型 MTP 支持标记必须是布尔值")
        for k, v in m.items():
            if isinstance(v, str) and any(x in v for x in ("\n", "\r", "\x00")):
                raise Error("模型字段不能包含换行或NUL")
    if not isinstance(c["runtime"].get("user"), str) or not re.fullmatch(
        r"[a-z_][a-z0-9_-]*", c["runtime"]["user"]
    ):
        raise Error("服务用户无效")
    home = c["runtime"].get("home")
    if (
        not isinstance(home, str)
        or not Path(home).is_absolute()
        or any(ord(x) < 32 for x in home)
    ):
        raise Error("服务home必须为不含控制字符的绝对路径")
    for m in c["catalog"]:
        for key in ("path", "vision_path", "mtp_path", "config_path"):
            if m.get(key) and (
                not isinstance(m[key], str) or not Path(m[key]).is_absolute()
            ):
                raise Error("模型文件必须为绝对路径：" + key)
        if m["backend"] == "ollama":
            source = m.get("source", "")
            if (
                not isinstance(source, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", source)
                or ".." in source.split("/")
            ):
                raise Error("Ollama模型名无效")


def warnings(c, b):
    out = []
    if b == "llama.cpp" and not value(c, "fit") and value(c, "reserve_mib"):
        out.append("fit关闭：llama.cpp 的 fit-target 不生效。")
    if value(c, "context") > 131072 and b in BACKENDS[:2]:
        out.append(
            "27B模型尚未验证此上下文的100% GPU容量；加载失败将回滚，不降低配置。"
        )
    if b == "strata" and value(c, "vision") and value(c, "reserve_mib") < 1536:
        out.append(
            "GPU视觉需要额外显存；当前预留较小，Strata可能将图片自动回退到CPU。保留默认值不等于保证GPU视觉。"
        )
    if b == "strata" and value(c, "context") > 262144:
        out.append("当前FN模型原生上限256K，超过上限不属于已验证范围。")
    return out


def address(host, port, client=False):
    if client:
        host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    return f"{host}:{port}"


def parse_setting(key, raw):
    if key not in SPECS:
        raise Error("未知设置：" + key)
    kind = SPECS[key][3]
    if kind == "int":
        try:
            return int(raw[:-1]) * 1024 if raw.lower().endswith("k") else int(raw)
        except (ValueError, AttributeError) as ex:
            raise Error("请输入整数，例如 128k、256k 或 4096") from ex
    if kind == "bool":
        if str(raw).lower() not in ("true", "false", "on", "off", "1", "0"):
            raise Error("布尔值应为 true/false")
        return str(raw).lower() in ("true", "on", "1")
    if isinstance(kind, tuple) and raw not in kind:
        raise Error("不支持的参数值：" + str(raw))
    return raw
