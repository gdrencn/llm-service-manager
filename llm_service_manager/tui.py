"""Inline terminal menus: preserve scrollback and never enter an alternate screen."""

import copy
import subprocess

from inline_ui import UI, Cancelled

from .availability import available_backends, model_present
from .commands import execute
from .config import SPECS, Error, model, parse_setting, validate, value
from .config_document import for_backend
from .discovery import discover
from .install_guide import choose_installation
from .inventory import inventory
from .lan_ui import show as show_lan
from .manager import release as release_model
from .manager import switch
from .network import port_adjustment
from .paths import CONFIG, PREFERENCES, STATE
from .preflight import preflight
from .presentation import (
    VERSION,
    cells,
    formatted,
    label,
    load_language,
    rich,
    set_language,
    t,
)
from .status import show as show_status
from .storage import read_snapshot


def main_menu():
    return [
        ("switch", t("切换底座/模型", "Switch backend/model")),
        ("commands", t("安装大模型和底座", "Install models and backends")),
        ("status", t("查看当前模型和服务状态", "Current model and service status")),
        ("release", t("释放资源", "Release resources")),
        ("lan", t("局域网访问", "LAN access")),
        ("options", t("选项", "Options")),
        ("exit", t("退出", "Exit")),
    ]


def options(ui):
    action = ui.choose(
        t("选项", "Options"),
        [
            ("language", t("语言", "Language")),
            ("version", t("版本信息", "Version information")),
            ("back", t("返回", "Back")),
        ],
    )
    if action == "language":
        from . import presentation

        lang = ui.choose(
            t("语言", "Language"),
            [("zh", "简体中文"), ("en", "English"), ("back", t("返回", "Back"))],
            focus=0 if presentation.LANGUAGE == "zh" else 1,
        )
        if lang != "back":
            set_language(lang)
    elif action == "version":
        ui.text(
            t("版本信息", "Version information"),
            f"llmsm {VERSION}\n"
            + t(
                "独立的本地模型服务配置工具。",
                "Independent local model service configuration tool.",
            )
            + "\n"
            + t("配置：", "Configuration: ")
            + str(CONFIG)
            + "\n"
            + t("界面偏好：", "UI preferences: ")
            + str(PREFERENCES)
            + "\n"
            + t("操作记录：", "Audit log: ")
            + str(STATE / "audit.jsonl"),
        )


def setting_rows(c, b):
    rows = []
    disabled = []
    width = max(cells(label(k)) for k in SPECS)
    for k, (_, _, applies, kind) in SPECS.items():
        state = t("可修改", "Editable")
        tone = "value"
        muted = False
        if b not in applies:
            state = t("不适用", "Not applicable")
            muted = True
        elif k == "gpu_only":
            state = t("固定", "Fixed")
            muted = True
        elif k == "reserve_mib" and b == "llama.cpp" and not value(c, "fit"):
            state = t("fit 关闭，不生效", "Inactive: fit off")
            muted = True
        elif k in ("mtp_candidates", "mtp_kv") and not value(c, "mtp"):
            state = t("MTP 关闭，不生效", "Inactive: MTP off")
            muted = True
        if muted:
            disabled.append(k)
            tone = "muted"
        if k == "port" and port_adjustment(c):
            state += t(
                "；默认55555被占用，自动增长至可用端口",
                "; default 55555 occupied, advanced to a free port",
            )
        text = label(k)
        padding = " " * (width - cells(text) + 2)
        rows.append(
            (
                k,
                rich(
                    (text + padding, "muted" if muted else "key"),
                    (formatted(value(c, k)), tone),
                    ("  [" + state + "]", "muted" if muted else "good"),
                ),
            )
        )
    rows += [
        (
            "review",
            rich(
                (t("应用配置并启动模型", "Apply configuration and start model"), "good")
            ),
        ),
        ("back", t("取消并退出", "Cancel and exit")),
    ]
    return rows, disabled


def edit(ui, c, b):
    c = for_backend(c, b)
    focus = 0
    while True:
        rows, disabled = setting_rows(c, b)
        key = ui.choose(
            t("配置 / ", "Settings / ") + b + " / " + model(c, b)["name"],
            rows,
            [
                *(
                    [
                        t(
                            f"默认55555被占用，已自动增长至可用端口 {port_adjustment(c)}。",
                            f"Default 55555 occupied; automatically selected {port_adjustment(c)}.",
                        )
                    ]
                    if port_adjustment(c)
                    else []
                ),
                t(
                    "地址、端口和自启动全局共用；其它参数仅保存到当前底座。",
                    "Address, port and autostart are global; other settings belong to this backend.",
                ),
            ],
            disabled,
            focus,
        )
        focus = next(i for i, r in enumerate(rows) if r[0] == key)
        if key == "back":
            raise Cancelled
        if key == "review":
            try:
                validate(c, b)
                return c
            except Error as ex:
                ui.text(t("配置错误", "Invalid configuration"), str(ex))
                continue
        _, _, _, kind = SPECS[key]
        try:
            if kind == "bool":
                v = ui.choose(
                    label(key),
                    [
                        (True, t("开启", "On")),
                        (False, t("关闭", "Off")),
                        ("back", t("取消", "Cancel")),
                    ],
                    focus=0 if value(c, key) else 1,
                )
            elif isinstance(kind, tuple):
                v = ui.choose(
                    label(key),
                    [(x, x) for x in kind] + [("back", t("取消", "Cancel"))],
                    focus=kind.index(value(c, key)),
                )
            else:
                raw = ui.input(label(key), value(c, key))
                v = parse_setting(key, raw)
            if v == "back":
                continue
            c["settings"][key]["value"] = v
            if key == "port":
                c.pop("initial_port", None)
        except (ValueError, Error):
            ui.text(
                t("输入无效", "Invalid input"),
                t(
                    "请输入有效数字；例如 128k、256k 或 4096。",
                    "Enter a valid number, such as 128k, 256k or 4096.",
                ),
            )
        except Cancelled:
            continue


def installation_list(ui, c):
    focus = 0
    while True:
        rows = inventory(c)
        selected = choose_installation(ui, rows, focus)
        if selected == len(rows) + 1:
            return
        if selected == -1:
            continue
        focus = selected
        entries = (
            [row["entry"] for row in rows if not row["installed"]]
            if selected == len(rows)
            else ([] if rows[selected]["installed"] else [rows[selected]["entry"]])
        )
        if not entries:
            continue
        ui.clear()
        try:
            execute("all", c, items=entries)
        except KeyboardInterrupt:
            ui.text(
                t("已中断", "Interrupted"),
                t(
                    "已中断执行，返回清单。",
                    "Execution interrupted; returning to the list.",
                ),
            )
        except (Error, OSError, ValueError, subprocess.SubprocessError) as ex:
            ui.text(
                t(
                    "命令失败，后续项未执行",
                    "Command failed; remaining items were not executed",
                ),
                str(ex),
            )


def selection_flow(ui, c, rev):
    installed = available_backends(c)
    if not installed:
        ui.text(
            t("没有可用底座", "No available backends"),
            t(
                "请先进入“安装大模型和底座”。",
                "Open Install models and backends first.",
            ),
        )
        return
    b = ui.choose(
        t("选择底座", "Choose backend"),
        [(b, b) for b in installed] + [("back", t("返回", "Back"))],
    )
    if b == "back":
        return
    choices = [
        (m["id"], m["name"])
        for m in c["catalog"]
        if m["backend"] == b and model_present(c, m)
    ]
    if not choices:
        ui.text(
            t("没有已安装的模型", "No installed models"),
            t(
                "请通过安装大模型和底座或底座官方流程下载模型，再回来选择。",
                "Download models using the installation guide or official backend tools, then return here.",
            ),
        )
        return
    ident = ui.choose(
        t("选择模型 / ", "Choose model / ") + b, choices + [("back", t("返回", "Back"))]
    )
    if ident == "back":
        return
    c = copy.deepcopy(c)
    c["model_selection"][b] = ident
    while True:
        c = edit(ui, c, b)
        errors, notes = preflight(c, b)
        if errors:
            ui.text(t("启动前检查未通过", "Pre-start checks failed"), "\n".join(errors))
            continue
        ui.external(
            lambda: switch(c, b, rev),
            t("应用配置并启动模型", "Apply configuration and start model"),
        )
        return


def release_resources(ui):
    rows = [
        (True, t("释放资源", "Release resources")),
        (False, t("返回", "Back")),
    ]
    if ui.choose(
        t("释放当前模型资源？", "Release current model resources?"),
        rows,
        [
            t(
                "卸载模型并保留服务；新请求会自动加载。请先结束正在处理的任务。",
                "Unloads models and keeps the service online. New requests reload them. Finish active tasks first.",
            )
        ],
        focus=1,
    ):

        def unload():
            release_model()
            print(
                t(
                    "模型已卸载或原本未加载；服务保持原状态，新推理请求可重新加载模型。",
                    "Models are unloaded or were already absent; service state is unchanged. New inference requests may reload models.",
                )
            )

        ui.external(unload, t("释放结果", "Release result"))


def main():
    load_language()
    ui = UI(translate=t)
    try:
        run_menu(ui)
    finally:
        ui.finish()


def run_menu(ui):
    while True:
        try:
            c, rev = read_snapshot()
            c = discover(c)
            target = c.get("target")
            try:
                selected = (
                    target["backend"]
                    + " / "
                    + model(c, target["backend"], target["model"])["name"]
                    if target
                    else t("尚未选择模型", "No model selected")
                )
            except Error:
                selected = t("已保存的模型不可用", "Saved model unavailable")
            action = ui.choose(
                "LLM Service Manager",
                main_menu(),
                [t("已保存的选择：", "Saved selection: ") + selected],
            )
            if action == "exit":
                return
            actions = {
                "switch": lambda: selection_flow(ui, c, rev),
                "options": lambda: options(ui),
                "lan": lambda: show_lan(ui),
                "commands": lambda: installation_list(ui, c),
                "status": lambda: ui.external(
                    lambda: show_status(c),
                    t("当前模型和服务状态", "Current model and service status"),
                ),
                "release": lambda: release_resources(ui),
            }
            actions[action]()
        except EOFError:
            return
        except Cancelled:
            continue
        except (Error, OSError, ValueError, subprocess.SubprocessError) as ex:
            ui.text(t("操作失败", "Operation failed"), str(ex))
