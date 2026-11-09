"""Read-only validation before any service or configuration mutation."""

import json
import os
from pathlib import Path

from .artifacts import (
    complete_asset,
    gguf_set,
    manifest_relative,
    ollama_manifest,
    strata_assets,
)
from .availability import available_backends, executable, program
from .config import model, validate, value, warnings
from .paths import ollama_roots


def preflight(c, b):
    validate(c, b)
    m = model(c, b)
    errors = []
    notes = warnings(c, b)
    home = Path(c["runtime"]["home"])
    if b not in available_backends(c):
        errors.append("底座未安装或引擎未准备好：" + b)

    def need(path, label, execute=False):
        try:
            p = Path(path) if isinstance(path, (str, Path)) and str(path) else None
            if p is None or not p.is_file():
                errors.append(label + "缺失：" + str(path))
                return
            if p.stat().st_size == 0:
                errors.append(label + "为空：" + str(path))
            if execute and not os.access(p, os.X_OK):
                errors.append(label + "不可执行：" + str(path))
        except (OSError, ValueError) as ex:
            errors.append(label + "不可读取：" + str(ex))

    if value(c, "mtp") and not m.get("mtp_supported"):
        errors.append("尚未确认该模型支持MTP；请关闭MTP后使用")
    if b == "llama.cpp":
        need(program(c, b), "llama程序", True)
        need(m.get("path"), "模型")
        if m.get("path") and not gguf_set(m["path"]):
            errors.append("GGUF 主模型或分片不完整")
        if value(c, "vision"):
            need(m.get("vision_path"), "视觉组件")
        if value(c, "mtp") and m.get("mtp_path"):
            need(m["mtp_path"], "MTP权重")
    elif b == "ollama":
        need(program(c, b), "Ollama程序", True)
        if not value(c, "vision"):
            errors.append("当前Ollama适配器不能禁用模型自带视觉组件；该项需保持开启")
        found = False
        for root in ollama_roots(home):
            if not (
                root / "manifests" / manifest_relative(m.get("source", ""))
            ).is_file():
                continue
            found = True
            try:
                ollama_manifest(root, m["source"])
            except (OSError, ValueError, TypeError) as ex:
                errors.append("模型manifest无效：" + str(ex))
            break
        if not found:
            errors.append(
                "本地Ollama模型未找到："
                + m.get("source", "")
                + "；请先执行官方pull命令"
            )
    else:
        need(m.get("config_path"), "Strata模型配置")
        try:
            cfg = json.loads(Path(m.get("config_path", "")).read_text())
            if (
                not isinstance(cfg, dict)
                or not isinstance(cfg.get("cwd"), str)
                or not isinstance(cfg.get("args"), list)
            ):
                raise ValueError("需要 cwd 和 args")
            if cfg.get("api_key"):
                raise ValueError(
                    "此版本暂不支持管理已启用 API Key 的 Strata 配置；不会移除其认证设置"
                )
            args = cfg["args"]
            if any(
                not isinstance(x, str) or any(ord(ch) < 32 for ch in x) for x in args
            ):
                raise ValueError("args必须为不含控制字符的字符串列表")
            cwd = Path(cfg["cwd"])
            if not cwd.is_absolute() or any(ord(ch) < 32 for ch in cfg["cwd"]):
                raise ValueError("cwd必须是有效绝对路径")
            exe = cfg.get("exe", "")
            if not isinstance(exe, str):
                raise ValueError("exe格式无效")
            if not exe.startswith("component:"):
                need(exe, "Strata引擎", True)

            for path in strata_assets(
                cfg, vision=value(c, "vision"), mtp=value(c, "mtp")
            ):
                if not complete_asset(path):
                    errors.append("Strata 模型文件缺失或不完整：" + str(path))
            if value(c, "vision"):
                vi = cfg.get("vision")
                if not isinstance(vi, dict):
                    errors.append("Strata缺少视觉配置")
                else:
                    need(vi.get("mmproj"), "Strata视觉权重")
                    ve = vi.get("exe", "")
                    if isinstance(ve, str) and ve.startswith("component:"):
                        if not any(
                            executable(p)
                            for p in (
                                home / ".local/share/strata/components/engine"
                            ).glob("*/strata-vision")
                        ):
                            errors.append("Strata视觉引擎未安装")
                    else:
                        need(ve, "Strata视觉引擎", True)
        except (OSError, ValueError, TypeError) as ex:
            errors.append("Strata运行配置无效：" + str(ex))
    return errors, notes
