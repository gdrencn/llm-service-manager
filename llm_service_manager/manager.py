"""Coordinate reversible configuration changes; backend lifecycle is delegated."""

import copy
import json
import tempfile
import time
from pathlib import Path

from .adapters import native
from .availability import available_backends
from .config import Error, model, value
from .execution import report_outcome
from .paths import CONFIG, UNITS
from .preflight import preflight
from .services import Services as Ops
from .storage import atomic, locked, revision
from .transaction import read_recovery, restore_file, snapshot


def stop(ops=None):
    """One locked stop operation shared by CLI and interactive bindings."""
    ops = ops or Ops()
    with locked(ops.state):
        ops.stop()


def release(ops=None, config_path=CONFIG):
    from .model_lifecycle import release as release_models
    from .storage import read_config

    ops = ops or Ops()
    with locked(ops.state):
        release_models(read_config(config_path), ops)


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
        if b == "llama.cpp":
            ops.check_router(c)
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
        backup, metadata = snapshot(files)
        txn = {
            "files": backup,
            "metadata": metadata,
            "config_path": str(config_path),
            "active": previous,
            "enabled": ops.enabled(),
            "target": c["target"],
            "started": time.time(),
            "runtime": ops.snapshot_runtime(config_path, previous),
        }
        atomic(pending, json.dumps(txn))
        try:
            ops.event("transaction-start", target=c["target"])
            ops.stop()
            ops.check_bind(c)
            for p, text in files.items():
                if p != str(config_path):
                    ops.write(p, text)
            ops.run(["systemctl", "daemon-reload"])
            ops.prepare_runtime(c, files)
            ops.run(["systemctl", "start", UNITS[b]], timeout=900)
            if b == "ollama":
                txn["ollama_model"] = ops.snapshot_ollama(c)
                atomic(pending, json.dumps(txn))
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
                report_outcome(ops, "rollback-failed", error=str(ex))
            raise
        report_outcome(ops, "transaction-committed", target=c["target"])


def recover(ops=None, lock=True):
    ops = ops or Ops()
    if lock:
        with locked(ops.state):
            return recover(ops, False)
    p = ops.state / "pending.json"
    if not p.exists():
        raise Error("没有待恢复事务")
    txn, restored = read_recovery(p)
    ops.stop()
    if txn.get("ollama_model") and not txn.get("ollama_restored"):
        ops.restore_ollama(txn["ollama_model"])
        # Durable compensation marker: a later recovery retry must not start
        # Ollama with candidate addresses after original files were restored.
        txn["ollama_restored"] = True
        atomic(p, json.dumps(txn))
        ops.stop()
    ops.clear_new_boot_links(txn)
    for path, data in restored.items():
        ops.event("restore", path=path)
        restore_file(path, data, txn.get("metadata", {}).get(path))
    ops.run(["systemctl", "daemon-reload"])
    ops.restore_boot(txn)
    for u in txn["active"]:
        ops.run(["systemctl", "start", u], timeout=900)
    ops.restored(txn)
    atomic(ops.state / "last-rollback.json", json.dumps(txn))
    p.unlink()
    report_outcome(ops, "rollback-completed")
