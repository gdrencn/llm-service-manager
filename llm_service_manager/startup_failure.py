"""Bounded startup diagnostics captured before rollback replaces the service."""

import re

from .config import Error, model
from .model_names import public_name
from .paths import UNITS
from .presentation import t


class StartupFailure(Error):
    pass


def excerpt(text, limit=6):
    # Strip terminal controls from native output before displaying it in the UI.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    lines = [
        "".join(ch for ch in line if ch.isprintable()).strip()
        for line in text.splitlines()
    ]
    relevant = [
        line for line in lines
        if re.search(r"\b(?:error|failed|fatal|exception)\b|\b[EW]\s|level=(?:ERROR|WARN)", line, re.I)
    ]
    selected = relevant[-limit:]
    return "\n".join("  " + line[:400] for line in selected)


def capture(ops, c, backend, error, started):
    """Diagnostics are best effort: never prevent transaction recovery."""
    details = ""
    try:
        invocation = ops.run(
            ["systemctl", "show", UNITS[backend], "--property=InvocationID", "--value"],
            False, timeout=5,
        ).stdout.strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", invocation) and int(invocation, 16):
            result = ops.run(
                ["journalctl", "_SYSTEMD_INVOCATION_ID=" + invocation,
                 "--since", "@" + str(started), "--no-pager", "-o", "cat", "-n", "160"],
                False, timeout=5,
            )
            if result.returncode == 0:
                details = excerpt(result.stdout)
    except Exception:
        # Includes inaccessible journald, command timeout, and audit I/O errors.
        pass
    message = t("模型启动失败：", "Model startup failed: ") + backend + " / " + public_name(model(c, backend))
    if details:
        message += "\n\n" + t("底座日志（原文节选）：", "Backend log excerpt:") + "\n" + details
    else:
        message += "\n\n" + t("未取得本次启动的错误日志。", "No error log was available for this startup.")
    brief = " ".join(str(error).split())
    message += "\n\n" + t("失败信息：", "Failure: ") + brief[:600]
    message += "\n" + t("操作记录：", "Operation log: ") + str(ops.state / "audit.jsonl")
    return StartupFailure(message)
