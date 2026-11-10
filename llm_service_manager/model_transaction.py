"""Snapshot and compensate native model changes within one apply transaction."""

import re
import tempfile
from pathlib import Path

from .availability import program
from .config import Error, address, model, normalized, validate, value
from .model_lifecycle import base_url, unloaded
from .model_names import public_name
from .paths import UNITS
from .storage import read_config


def runtime_snapshot(config_path, active, ops):
    if not active:
        return None
    before = read_config(config_path)
    selected = before.get("target")
    if not selected or active != [UNITS[selected["backend"]]]:
        return None
    backend = selected["backend"]
    return {"backend": backend, "loaded": not unloaded(ops, base_url(before), backend)}


def ollama_snapshot(c, ops):
    ops.wait_ready(c, "ollama")
    name = public_name(model(c, "ollama"))
    executable = program(c, "ollama")
    if executable is None:
        raise Error("Ollama 程序不存在，未修改模型")
    text = ops.run(
        [
            "env",
            "OLLAMA_HOST=" + address(value(c, "host"), value(c, "port"), True),
            executable,
            "show",
            "--modelfile",
            name,
        ]
    ).stdout
    if not re.search(r"(?im)^FROM\s+\S+", text):
        raise Error("无法取得 Ollama 修改前的完整 Modelfile，未修改模型")
    return {
        "name": name,
        "modelfile": text,
        "host": value(c, "host"),
        "port": value(c, "port"),
    }


def rollback_config(record):
    if not isinstance(record, dict) or set(record) != {
        "name",
        "modelfile",
        "host",
        "port",
    }:
        raise ValueError("Ollama 模型恢复记录格式错误")
    if not isinstance(record["modelfile"], str) or not re.search(
        r"(?im)^FROM\s+\S+", record["modelfile"]
    ):
        raise ValueError("Ollama 模型恢复记录缺少 Modelfile")
    c = normalized(
        {
            "settings": {
                "host": {"value": record["host"]},
                "port": {"value": record["port"]},
            },
            "catalog": [
                {
                    "id": "rollback",
                    "backend": "ollama",
                    "name": record["name"],
                    "source": record["name"],
                }
            ],
            "model_selection": {"ollama": "rollback"},
        }
    )
    validate(c)
    return c


def restore_ollama(record, ops):
    c = rollback_config(record)
    ops.run(["systemctl", "start", UNITS["ollama"]], timeout=900)
    ops.wait_ready(c, "ollama")
    with tempfile.TemporaryDirectory(prefix="llmsm-model-rollback-") as directory:
        path = Path(directory) / "model.Modelfile"
        ops.write(path, record["modelfile"])
        ops.create_ollama_model(c, str(path))
