"""LAN observations and checked setup actions; opening the guide never applies changes."""

import copy
import ipaddress
import json
import os
import shlex
import shutil
import socket
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .checklist import label as state_label
from .checklist import state as checklist_state
from .config import Error, value
from .execution import Operations
from .presentation import t


@dataclass(frozen=True)
class Step:
    title: str
    command: str
    description: str
    checked: bool | None = None
    action: tuple[str, ...] | None = None

    @property
    def state(self):
        return checklist_state(self.checked, self.action is not None)


def probe_local(c, ops=None):
    ops = ops or Operations(quiet=True)
    port = value(c, "port")
    result = {
        "wsl": "microsoft" in os.uname().release.lower(),
        "distro": os.environ.get("WSL_DISTRO_NAME"),
        "ip": None,
        "listening": None,
        "windows": None,
        "errors": [],
        "powershell": None,
        "network_mode": None,
    }
    try:
        lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
        result["listening"] = [
            socket.inet_ntoa(bytes.fromhex(row[1].split(":")[0])[::-1])
            for line in lines
            if (row := line.split())[3] == "0A"
            and int(row[1].split(":")[1], 16) == port
        ]
    except (OSError, ValueError, IndexError) as ex:
        result["errors"].append(str(ex))
    if shutil.which("ip"):
        try:
            # Route lookup only; it sends no traffic to the destination.
            routes = json.loads(
                ops.run(["ip", "-j", "-4", "route", "get", "1.1.1.1"], timeout=5).stdout
            )
            candidate = routes[0].get("prefsrc")
            if candidate and not ipaddress.ip_address(candidate).is_loopback:
                result["ip"] = str(ipaddress.IPv4Address(candidate))
        except (
            Error,
            OSError,
            ValueError,
            IndexError,
            TypeError,
            AttributeError,
        ) as ex:
            result["errors"].append(str(ex))
    from .linux_lan import inspect

    result["linux_plan"] = asdict(inspect(port, ops))
    if not result["wsl"]:
        return result
    wslinfo = shutil.which("wslinfo") or (
        "/usr/lib/wsl/wslinfo" if Path("/usr/lib/wsl/wslinfo").is_file() else None
    )
    if wslinfo:
        try:
            result["network_mode"] = (
                ops.run([wslinfo, "--networking-mode"], timeout=5)
                .stdout.strip()
                .lower()
            )
        except (Error, OSError) as ex:
            result["errors"].append(str(ex))
    return result


def probe_windows(c, ops=None):
    ops = ops or Operations(quiet=True)
    port = value(c, "port")
    result = {"windows": None, "powershell": None, "errors": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        for path in [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
        ]:
            if Path(path).is_file():
                powershell = path
                break
    if not powershell:
        result["errors"].append(
            t(
                "无法访问 Windows PowerShell；请手动执行 Windows 命令。",
                "Windows PowerShell unavailable; Windows state is unverified.",
            )
        )
        return result
    result["powershell"] = powershell
    name = f"llmsm-LAN-TCP-{port}"
    script = r"""$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new();
$proxy=(& netsh interface portproxy show v4tov4 | Out-String);
if ($LASTEXITCODE -ne 0) { throw 'portproxy query failed' };
$rules=@(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object Name -eq 'RULE_NAME' | ForEach-Object {
$p=$_ | Get-NetFirewallPortFilter; $a=$_ | Get-NetFirewallAddressFilter;
@{enabled=[string]$_.Enabled; direction=[string]$_.Direction; action=[string]$_.Action;
profile=[string]$_.Profile; protocol=[string]$p.Protocol; ports=@($p.LocalPort); remote=@($a.RemoteAddress)} });
$listeners=@(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq PORT_VALUE | Select-Object LocalAddress,LocalPort,OwningProcess);
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
@{portproxy=$proxy; rules=$rules; listeners=$listeners; admin=$admin} | ConvertTo-Json -Depth 6 -Compress
""".replace("RULE_NAME", name).replace("PORT_VALUE", str(port))
    try:
        data = json.loads(
            ops.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=12,
            ).stdout.lstrip("\ufeff")
        )
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("portproxy"), str)
            or not isinstance(data.get("rules"), list)
        ):
            raise ValueError("Invalid Windows network snapshot")
        result["windows"] = data
    except (Error, OSError, ValueError) as ex:
        result["errors"].append(str(ex))
    return result


def merge_probes(local, windows):
    result = dict(local)
    result.update(windows)
    result["errors"] = local["errors"] + windows["errors"]
    return result


def probe(c, ops=None):
    """Fresh synchronous probe used for CLI and immediately before a mutation."""
    ops = ops or Operations(quiet=True)
    local = probe_local(c, ops)
    return merge_probes(local, probe_windows(c, ops)) if local["wsl"] else local


def _proxy_rows(text):
    result = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            result.append(
                (
                    str(ipaddress.IPv4Address(parts[0])),
                    int(parts[1]),
                    str(ipaddress.IPv4Address(parts[2])),
                    int(parts[3]),
                )
            )
        except ValueError:
            pass
    return result


def _firewall_matches(rule, port):
    return isinstance(rule, dict) and all(
        [
            rule.get("enabled") == "True",
            rule.get("direction") == "Inbound",
            rule.get("action") == "Allow",
            rule.get("profile") == "Private",
            rule.get("protocol") in ("TCP", "6"),
            rule.get("ports") == [str(port)],
            rule.get("remote") == ["LocalSubnet"],
        ]
    )


def steps(c, snapshot):
    host, port = value(c, "host"), value(c, "port")
    ip = snapshot.get("ip")
    rows = []
    try:
        externally_bound = not ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        externally_bound = False
    rows.append(
        Step(
            t("底座监听配置", "Backend listen configuration"),
            "llmsm",
            t(
                f"当前保存：{host}:{port}。进入“切换底座/模型”设置 0.0.0.0，再应用启动；保存值不代表实际监听。",
                f"Saved: {host}:{port}. Set 0.0.0.0 under Switch backend/model and apply; saved values are not live listeners.",
            ),
            externally_bound,
        )
    )
    listeners = snapshot.get("listening")
    exposed = bool(
        listeners and any(not ipaddress.ip_address(a).is_loopback for a in listeners)
    )
    rows.append(
        Step(
            t("Linux TCP 监听", "Linux TCP listener"),
            f"ss -ltn 'sport = :{port}'",
            t(
                "仅检测当前端口是否存在非回环 IPv4 监听，不确认程序身份，也不等于局域网已通。",
                "Checks non-loopback IPv4 TCP listeners only, not process identity or LAN reachability.",
            ),
            exposed if listeners is not None else None,
        )
    )
    rows.append(
        Step(
            t("Linux 地址", "Linux address"),
            "ip -4 addr show scope global",
            t("默认路由的本机 IPv4：", "Local IPv4 on the default route: ")
            + (
                ip
                or t("未取得，请手动执行命令", "Unavailable; run the command manually")
            ),
            True if ip else None,
        )
    )
    rows.append(
        Step(
            t("Linux 防火墙", "Linux firewall"),
            "llmsm lan --configure-linux",
            "检测发行版工具和原生防火墙状态。",
        )
    )
    if snapshot["wsl"]:
        distro = snapshot.get("distro") or "<发行版名称>"
        quoted = "'" + distro.replace("'", "''") + "'"
        rows.append(
            Step(
                t(
                    "Windows：确认 NAT 和局域网地址",
                    "Windows: confirm NAT and LAN address",
                ),
                f'Get-Content "$env:USERPROFILE\\.wslconfig" -ErrorAction SilentlyContinue\nGet-NetIPConfiguration\nGet-NetConnectionProfile\nwsl -d {quoted} hostname -I',
                t(
                    "在 Windows PowerShell 执行。下面转发步骤用于 NAT；不要把 WSL 网关当作 Windows 局域网 IP。若已有 mirrored 配置，不要照搬 NAT 转发。",
                    "Run in Windows PowerShell. Forwarding below is for NAT; the WSL gateway is not the Windows LAN IP. Do not apply NAT forwarding to mirrored networking.",
                ),
            )
        )
        target_ip = ip or "<WSL_IPV4>"
        win = snapshot.get("windows")
        rules = _proxy_rows(win["portproxy"]) if win else []
        listeners = win.get("listeners") if win else None
        rows.append(
            Step(
                t("Windows：端口占用", "Windows: port ownership"),
                f"Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue\nnetsh interface ipv4 show excludedportrange protocol=tcp",
                t(
                    "无 TCP 监听不代表端口未被系统保留；有监听时核对 OwningProcess，可能是已有转发。此项与 Linux 端口探测独立。",
                    "No TCP listener does not exclude a Windows reserved port. Check OwningProcess when occupied; it may be an existing forwarder. This is separate from the Linux port probe.",
                ),
                True if listeners == [] else None,
            )
        )
        match = ("0.0.0.0", port, target_ip, port) in rules
        rows.append(
            Step(
                t("Windows：TCP 转发", "Windows: TCP forwarding"),
                "netsh interface portproxy show v4tov4",
                t(
                    "只读检查目标转发是否匹配；配置请使用下方一键命令。WSL IP 改变后需重跑。",
                    "Read-only forwarding check. Use the one-command setup below; rerun after a WSL IP change.",
                ),
                match if win and ip else None,
            )
        )
        name = f"llmsm-LAN-TCP-{port}"
        good = bool(win and any(_firewall_matches(rule, port) for rule in win["rules"]))
        command = f"Get-NetFirewallRule -PolicyStore ActiveStore -Name '{name}' -ErrorAction SilentlyContinue | Format-List"
        rows.append(
            Step(
                t(
                    "Windows：专用网络防火墙规则",
                    "Windows: private-network firewall rule",
                ),
                command,
                t(
                    "只读检查具名防火墙规则；配置请使用下方一键命令，仅开放专用网络和本地子网。勾不排除其它阻断规则。",
                    "Read-only named firewall rule check; use the setup command below for Private networks and LocalSubnet. A check does not rule out other blocking rules.",
                ),
                good if win else None,
            )
        )
        from .windows_lan import command as windows_command

        rows.append(
            Step(
                t("Windows：一键配置", "Windows: one-command setup"),
                windows_command(),
                t(
                    "在普通 Windows PowerShell 粘贴执行，按 UAC 提示授权；逐步检测 NAT、端口及服务，再配置原生转发和防火墙。WSL 地址变化后重跑。不创建常驻服务。发行版和端口由脚本交互选择。",
                    "Paste into ordinary Windows PowerShell and approve UAC. Checks NAT, port ownership and service reachability, then configures native forwarding and firewall. Rerun after WSL IP changes. No daemon. Choose the distribution and port interactively.",
                ),
                bool(match and good and snapshot.get("network_mode") == "nat")
                if win and ip
                else None,
            )
        )
        target = "<Windows局域网IPv4>"
    else:
        target = ip or "<Linux局域网IPv4>"
    rows.append(
        Step(
            t("从另一台局域网电脑验证", "Verify from another LAN computer"),
            f"# Windows PowerShell\ncurl.exe --connect-timeout 5 http://{target}:{port}/v1/models\n# Linux / macOS\ncurl --connect-timeout 5 http://{target}:{port}/v1/models",
            t(
                "选择对应操作系统的命令，替换尖括号占位符。客户端 Base URL 使用同地址加 /v1；必须在另一台电脑执行，当前机器无法证明这段链路已通。",
                "Choose the command for the client OS and replace angle-bracket placeholders. Client Base URL uses this address with /v1. Run from another computer; this host cannot prove that network path works.",
            ),
        )
    )
    return bind_actions(c, snapshot, rows)


def bind_actions(c, snapshot, rows):
    target = c.get("target")
    apply = None
    if target and os.geteuid() == 0:
        from .preflight import preflight

        candidate = copy.deepcopy(c)
        candidate["settings"]["host"]["value"] = "0.0.0.0"
        candidate["model_selection"][target["backend"]] = target["model"]
        try:
            errors, _ = preflight(candidate, target["backend"])
        except (Error, OSError, ValueError):
            errors = ["unavailable"]
        if not errors:
            apply = (
                "llmsm",
                "switch",
                target["backend"],
                "--model",
                target["model"],
                "--set",
                "host=0.0.0.0",
                "--yes",
            )
    for index in (0, 1):
        if rows[index].checked is False and apply:
            rows[index] = replace(
                rows[index],
                command=shlex.join(apply),
                action=apply,
                description=rows[index].description
                + t(
                    " 执行会应用配置并重启当前选定底座。",
                    " Execution applies configuration and restarts the selected backend.",
                ),
            )
    plan = snapshot.get("linux_plan")
    if plan:
        action = (
            ("llmsm", "lan", "--configure-linux")
            if plan["commands"] and os.geteuid() == 0
            else None
        )
        rows[3] = replace(
            rows[3],
            command="llmsm lan --configure-linux"
            + (
                "\n# 本轮官方命令；安装后会重新检测：\n"
                + "\n".join(shlex.join(cmd) for cmd in plan["commands"])
                if plan["commands"]
                else ""
            ),
            description=plan["reason"],
            checked=bool(plan["ready"]),
            action=action,
        )
        if not snapshot.get("ip") and plan["commands"]:
            rows[2] = replace(
                rows[2],
                command=rows[3].command,
                action=action,
                checked=False,
                description="先补齐网络工具；不会修改 IP、默认路由或 DNS。",
            )
    return rows


def plain(c, snapshot):
    lines = [
        t(
            "局域网访问：检查通过为已生效，可自动完成为执行，其余请手动执行命令。",
            "LAN access: verified, executable, or manual according to one shared policy.",
        )
    ]
    for row in steps(c, snapshot):
        lines += [
            "",
            f"[{state_label(row.state)[0]}] {row.title}",
            row.description,
            row.command,
        ]
    lines += ["", *snapshot.get("errors", [])]
    return "\n".join(lines)
