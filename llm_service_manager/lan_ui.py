"""Two LAN entry points; UI binds independent server setup and Windows guidance."""

import os

from inline_ui.style import rich

from .config import Error, value
from .presentation import t
from .server_setup import complete
from .storage import read_snapshot
from .windows_lan import command


def is_wsl():
    return "microsoft" in os.uname().release.lower()


def windows_content(c):
    port = value(c, "port")
    return [
        rich((t("一键命令", "Setup command"), "group")),
        rich((command(), "command")),
        "",
        t(
            f"本机当前服务端口：{port}。运行脚本后选择发行版，并输入对应端口。",
            f"Current server port: {port}. Select the distribution and enter its port in the script.",
        ),
        t(
            "复制到 Windows 普通 PowerShell 执行，在原窗口选择发行版和端口，再根据 UAC 提示授权。",
            "Paste into ordinary Windows PowerShell then choose the distribution and port in the same console before approving UAC.",
        ),
        "",
        rich((t("环境要求", "Requirements"), "group")),
        t(
            "Windows 11 24H2 或更新，已启用微软 Sudo for Windows 的 Inline 模式。未启用时会提示用管理员 PowerShell 执行 sudo config --enable normal 后重跑；缺组件或策略限制分别说明。",
            "Windows 11 24H2 or later with Microsoft Sudo for Windows enabled in Inline mode. If disabled, run sudo config --enable normal in administrator PowerShell and rerun; missing components and policy restrictions get separate guidance.",
        ),
        "",
        rich((t("执行内容", "What it does"), "group")),
        t(
            "先检查 WSL NAT、Linux 服务、专用网络和端口占用，再配置 Windows 原生 TCP 转发及防火墙；逐步骤解释目的与结果。",
            "Checks WSL NAT, the Linux service, Private networks and port ownership, then configures native Windows forwarding and firewall with step-by-step explanations.",
        ),
        t(
            "仅放行该 TCP 端口的专用网络、本地子网；不修改其它规则，不创建后台程序或计划任务。",
            "Allows this TCP port for Private networks and LocalSubnet only; no unrelated rule changes, daemon or scheduled task.",
        ),
        "",
        rich((t("使用说明", "Usage"), "group")),
        t(
            "先完成服务器端设置。WSL IP 改变后重新运行本命令；Windows 重启后需启动 WSL 和模型服务。",
            "Complete server setup first. Rerun after the WSL IP changes; WSL and the model must be running after a Windows reboot.",
        ),
        t(
            "适用于 WSL2 NAT。其它网络模式会停止并说明原因；专用网络需由用户确认是可信局域网。",
            "For WSL2 NAT. Other modes stop with an explanation; you must identify your trusted LAN as Private.",
        ),
        t(
            "最后在另一台电脑使用脚本输出的地址访问 /v1/models；本机检查不等于整条局域网链路已通。",
            "Finally check /v1/models from another computer using the printed address; local checks cannot prove the entire LAN path.",
        ),
    ]


def show(ui):
    while True:
        items = [("server", t("完成服务器端设置", "Complete server setup"))]
        if is_wsl():
            items.append(
                ("windows", t("Windows端设置(WSL专属)", "Windows setup (WSL only)"))
            )
        items.append(("back", t("返回", "Back")))
        selected = ui.choose(t("局域网访问", "LAN access"), items)
        if selected == "back":
            return
        try:
            if selected == "server":
                ui.external(complete, t("完成服务器端设置", "Complete server setup"))
            elif selected == "windows":
                c, _ = read_snapshot()
                ui.document(
                    t("Windows端设置(WSL专属)", "Windows setup (WSL only)"),
                    windows_content(c),
                    [("back", t("返回", "Back"))],
                )
        except (Error, OSError, ValueError) as ex:
            ui.text(t("设置未完成", "Setup incomplete"), str(ex))
