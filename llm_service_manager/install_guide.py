"""Pure installation list rendering."""

from inline_ui.style import wrap

from .commands import explanation
from .presentation import cells, rich, t


def list_blocks(rows, width, focus):
    """Full-width commands; status/action follows the explanation, no grid."""
    width = max(12, width - 1)
    blocks = []
    for index, row in enumerate(rows):
        backend, title, cmd = row["entry"]
        done = row["installed"]
        block = [
            rich(
                (("› " if focus == index else "  "), "plain"),
                (backend, "group"),
                (" · " + title, "plain"),
            )
        ]
        block += [rich(("  " + part, "command")) for part in wrap(cmd, width - 2)]
        description = wrap(explanation(backend, cmd), width - 2)
        block += [rich(("  " + part, "muted")) for part in description[:-1]]
        tail = "  " + description[-1]
        action = t("✓ 已安装", "✓ Installed") if done else t("执行", "Execute")
        if cells(tail) + cells(action) + 3 > width:
            block.append(rich((tail, "muted")))
            tail = ""
        gap = " " * max(3, width - cells(tail) - cells(action))
        block.append(
            rich(
                (tail + gap, "muted"),
                (
                    action,
                    "good" if done else "highlight" if focus == index else "action",
                ),
            )
        )
        block.append("")
        blocks.append(block)
    missing = any(not row["installed"] for row in rows)
    text = t("自动安装全部项", "Install all automatically") + t(
        "（按顺序，仅未安装项；出错中断）",
        " (sequential, missing items only; stop on error)",
    )
    if not missing:
        text += " · " + t("✓ 已全部安装", "✓ All installed")
    blocks.append(
        [
            rich(
                (
                    ("› " if focus == len(rows) else "  ") + part,
                    "highlight"
                    if focus == len(rows) and missing
                    else "muted"
                    if not missing
                    else "action",
                )
            )
            for part in wrap(text, width - 2)
        ]
    )
    blocks.append(
        [
            rich(
                (
                    ("› " if focus == len(rows) + 1 else "  ") + t("返回", "Back"),
                    "highlight" if focus == len(rows) + 1 else "",
                )
            )
        ]
    )
    return blocks


def choose_installation(ui, rows, focus=0):
    """Bind installation content/status to the generic block selector."""
    missing = any(not row["installed"] for row in rows)
    options = [
        (
            n,
            row["entry"][0]
            + " / "
            + row["entry"][2]
            + " / "
            + (
                t("✓ 已安装", "✓ Installed")
                if row["installed"]
                else t("执行", "Execute")
            ),
        )
        for n, row in enumerate(rows)
    ]
    options += [
        (len(rows), t("自动安装全部项", "Install all automatically")),
        (len(rows) + 1, t("返回", "Back")),
    ]
    disabled = [n for n, row in enumerate(rows) if row["installed"]]
    if not missing:
        disabled.append(len(rows))
    return ui.blocks(
        t("安装大模型和底座", "Install models and backends"),
        options,
        lambda width, selected: list_blocks(rows, width, selected),
        details=[
            t(
                "↑↓ 选择 · Enter 执行 · R 刷新 · Esc 返回",
                "↑↓ Select · Enter Execute · R Refresh · Esc Back",
            )
        ],
        footer=[
            rich(
                (
                    t(
                        "绿色勾：本地已安装；只执行显示的命令。",
                        "Green check: installed locally; only displayed commands are executed.",
                    ),
                    "muted",
                )
            ),
            rich(
                (
                    t(
                        "普通用户主目录 / Bash pipefail；输出及命令会记录",
                        "User home / Bash pipefail; commands and output are logged",
                    ),
                    "muted",
                )
            ),
        ],
        disabled=disabled,
        focus=focus,
        refresh=-1,
    )
