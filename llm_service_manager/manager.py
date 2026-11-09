"""Coordinate reversible configuration changes; backend lifecycle is delegated."""

import base64
import copy
import json
import tempfile
import time
from pathlib import Path

from .adapters import native
from .availability import available_backends
from .config import Error, model, value
from .native_names import remove, retired_files
from .paths import ALL_UNITS, CONFIG, LEGACY_UNITS, UNITS
from .preflight import preflight
from .services import Services as Ops
from .storage import atomic, locked, revision


def stop(ops=None):
    """One locked stop operation shared by CLI and interactive bindings."""
    ops = ops or Ops()
    with locked(ops.state):
        ops.stop()


def switch(c, b, expected, ops=None, config_path=CONFIG):
    ops = ops or Ops()
    state = ops.state
    with locked(state):
        if revision(config_path) != expected:
            raise Error("配置已被另一个操作修改，请重新打开")
        pending = state / "pending.json"
        if pending.exists():
            raise Error("上次切换未完成，请先运行 llmsm recover")
        errors, notes = preflight(c, b)
        if errors:
            raise Error("\n".join(errors))
        for text in notes:
            ops.event("warning", message=text)
        installed = available_backends(c)
        files = native(c, backends=[b])
        for other in installed:
            if other == b:
                continue
            try:
                files.update(native(c, backends=[other]))
            except (Error, OSError, ValueError, KeyError, TypeError) as ex:
                ops.event(
                    "warning",
                    message="跳过其它底座的不可用配置：" + other + " / " + str(ex),
                )
        c = copy.deepcopy(c)
        c["target"] = {"backend": b, "model": model(c, b)["id"]}
        files[str(config_path)] = json.dumps(c, ensure_ascii=False, indent=2) + "\n"
        retired = retired_files(files)
        files.update(retired)
        # Verify the selected generated unit before touching the live services.
        unit_path = "/etc/systemd/system/" + UNITS[b]
        if unit_path in files:
            with tempfile.TemporaryDirectory(prefix="llm-unit-check-") as td:
                check = Path(td) / UNITS[b]
                ops.write(check, files[unit_path])
                ops.run(["systemd-analyze", "verify", "--man=no", str(check)])
        previous = ops.active()
        if len(previous) > 1:
            raise Error("检测到多个底座同时运行，请先停止后再切换")
        for path in files:
            if Path(path).is_symlink():
                raise Error("拒绝覆盖符号链接配置：" + path)
        backup = {
            p: base64.b64encode(Path(p).read_bytes()).decode()
            if Path(p).exists()
            else None
            for p in files
        }
        txn = {
            "files": backup,
            "config_path": str(config_path),
            "active": previous,
            "enabled": ops.enabled(),
            "target": c["target"],
            "started": time.time(),
        }
        atomic(pending, json.dumps(txn))
        try:
            ops.event("transaction-start", target=c["target"])
            ops.stop()
            ops.check_bind(c)
            for unit in LEGACY_UNITS.values():
                if "/etc/systemd/system/" + unit in retired:
                    ops.run(["systemctl", "disable", unit])
            for p, text in files.items():
                if p != str(config_path):
                    if text is None:
                        remove(ops, p)
                    else:
                        ops.write(p, text)
            ops.run(["systemctl", "daemon-reload"])
            ops.prepare_runtime(c, files)
            ops.run(["systemctl", "start", UNITS[b]], timeout=900)
            ops.ready(c, b)
            if ops.active() != [UNITS[b]]:
                raise Error("底座互斥检查未通过")
            ops.boot_target(b, value(c, "autostart"))
            ops.write(config_path, files[str(config_path)])
            atomic(state / "last-success.json", json.dumps(txn))
            pending.unlink()
        except BaseException:
            try:
                recover(ops, lock=False)
            except BaseException as ex:
                ops.event("rollback-failed", error=str(ex))
            raise
        try:
            ops.event("transaction-committed", target=c["target"])
        except OSError as ex:
            import sys

            print("配置已生效，但提交日志写入失败：" + str(ex), file=sys.stderr)


def recover(ops=None, lock=True):
    ops = ops or Ops()
    if lock:
        with locked(ops.state):
            return recover(ops, False)
    p = ops.state / "pending.json"
    if not p.exists():
        raise Error("没有待恢复事务")
    try:
        txn = json.loads(p.read_text())
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
        config_path = txn.get("config_path", str(CONFIG))
        if not isinstance(config_path, str) or not Path(config_path).is_absolute():
            raise ValueError("事务配置路径无效")
        restored = {}
        for path, data in txn["files"].items():
            if not Path(path).is_absolute():
                raise ValueError("事务路径不是绝对路径")
            if Path(path).is_symlink():
                raise ValueError("拒绝恢复符号链接配置：" + path)
            if data is not None and not isinstance(data, str):
                raise ValueError("事务内容格式错误")
            restored[path] = (
                None if data is None else base64.b64decode(data, validate=True)
            )
    except (ValueError, TypeError, OSError) as ex:
        raise Error("恢复记录无效，未修改服务：" + str(ex)) from ex
    ops.stop()
    for path, data in restored.items():
        ops.event("restore", path=path)
        if data is None:
            Path(path).unlink(missing_ok=True)
        else:
            atomic(path, data)
    ops.run(["systemctl", "daemon-reload"])
    ops.restore_boot(txn)
    for u in txn["active"]:
        ops.run(["systemctl", "start", u], timeout=900)
    ops.restored(txn)
    atomic(ops.state / "last-rollback.json", json.dumps(txn))
    p.unlink()
    ops.event("rollback-completed")
