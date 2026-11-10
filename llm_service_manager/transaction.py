"""File snapshots and recovery validation; no service lifecycle or UI dependencies."""

import base64
import json
import stat
from pathlib import Path

from .config import Error
from .paths import ALL_UNITS, CONFIG
from .storage import atomic


def snapshot(files):
    contents, metadata = {}, {}
    for name in files:
        path = Path(name)
        if path.is_symlink():
            raise Error("拒绝覆盖符号链接配置：" + name)
        if path.exists():
            info = path.stat()
            contents[name] = base64.b64encode(path.read_bytes()).decode()
            metadata[name] = {
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
        else:
            contents[name] = None
    return contents, metadata


def read_recovery(path):
    try:
        txn = json.loads(Path(path).read_text())
        if (
            not isinstance(txn, dict)
            or not isinstance(txn.get("files"), dict)
            or not isinstance(txn.get("active"), list)
        ):
            raise ValueError("事务字段格式错误")
        if any(u not in ALL_UNITS for u in txn["active"]):
            raise ValueError("事务包含未知服务")
        enabled = txn.get("enabled", {})
        if not isinstance(enabled, dict) or any(
            u not in ALL_UNITS or not isinstance(state, str)
            for u, state in enabled.items()
        ):
            raise ValueError("事务自启动状态格式错误")
        if "enabled" not in txn:
            raise ValueError("事务缺少操作前的自启动状态")
        runtime = txn.get("runtime")
        if runtime is not None:
            from .paths import UNITS

            if (
                not isinstance(runtime, dict)
                or runtime.get("backend") not in UNITS
                or type(runtime.get("loaded")) is not bool
                or txn["active"] != [UNITS[runtime["backend"]]]
            ):
                raise ValueError("事务模型驻留状态无效")
        if "ollama_model" in txn:
            from .model_transaction import rollback_config

            rollback_config(txn["ollama_model"])
        if "ollama_restored" in txn and type(txn["ollama_restored"]) is not bool:
            raise ValueError("事务模型补偿状态无效")
        config_path = txn.get("config_path", str(CONFIG))
        if not isinstance(config_path, str) or not Path(config_path).is_absolute():
            raise ValueError("事务配置路径无效")
        if txn["active"] and config_path not in txn["files"]:
            raise ValueError("事务缺少恢复运行服务所需的配置快照")
        metadata = txn.get("metadata", {})
        if not isinstance(metadata, dict) or set(metadata) - set(txn["files"]):
            raise ValueError("事务文件属性格式错误")
        restored = {}
        for name, data in txn["files"].items():
            if not Path(name).is_absolute():
                raise ValueError("事务路径不是绝对路径")
            if Path(name).is_symlink():
                raise ValueError("拒绝恢复符号链接配置：" + name)
            if data is not None and not isinstance(data, str):
                raise ValueError("事务内容格式错误")
            if name in metadata:
                info = metadata[name]
                if (
                    not isinstance(info, dict)
                    or set(info) != {"mode", "uid", "gid"}
                    or any(type(v) is not int or v < 0 for v in info.values())
                    or info["mode"] > 0o7777
                ):
                    raise ValueError("事务文件权限格式错误")
            restored[name] = (
                None if data is None else base64.b64decode(data, validate=True)
            )
        return txn, restored
    except (ValueError, TypeError, OSError) as ex:
        raise Error("恢复记录无效，未修改服务：" + str(ex)) from ex


def restore_file(path, data, metadata=None):
    if data is None:
        Path(path).unlink(missing_ok=True)
    else:
        atomic(path, data, metadata=metadata)
