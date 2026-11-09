"""Distro packages and native firewall operations; no resident llmsm component."""

import json
import re
import shutil
from dataclasses import dataclass

from .config import Error
from .execution import Operations


@dataclass(frozen=True)
class Plan:
    ready: bool
    reason: str
    commands: tuple[tuple[str, ...], ...] = ()


def packages(names):
    """Use configured distro repositories; never bootstrap a third-party manager."""
    if shutil.which("apt-get"):
        return (("apt-get", "update"), ("apt-get", "install", "-y", *names))
    if shutil.which("dnf"):
        return (("dnf", "install", "-y", *names),)
    return ()


def inspect(port, ops=None):
    ops = ops or Operations(quiet=True)
    try:
        return _inspect(port, ops)
    except (
        Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
    ) as ex:
        return Plan(False, "检测失败，未修改系统：" + str(ex))


def _inspect(port, ops):
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise Error("无效的 TCP 端口")
    missing = []
    if not shutil.which("ip") or not shutil.which("ss"):
        missing.append("iproute2" if shutil.which("apt-get") else "iproute")
    if missing:
        return Plan(
            False, "安装发行版标准网络工具，再检测地址和防火墙。", packages(missing)
        )

    ufw = None
    if shutil.which("ufw"):
        ufw = ops.run(["env", "LC_ALL=C", "ufw", "status"], timeout=5).stdout
        if not ufw.startswith(("Status: active", "Status: inactive")):
            raise Error("无法识别 UFW 状态")
    firewalld = False
    if shutil.which("firewall-cmd"):
        result = ops.run(["firewall-cmd", "--state"], check=False, timeout=5)
        if result.returncode not in (0, 252):
            # firewalld NOT_RUNNING is 252; other errors must not imply inactive.
            raise Error(result.stdout.strip() or "无法检测 firewalld 状态")
        firewalld = result.returncode == 0
    if ufw and ufw.startswith("Status: active"):
        if firewalld:
            return Plan(
                False,
                "UFW 和 firewalld 同时启用；请先明确由哪个防火墙管理，避免规则冲突。",
            )
        if re.search(r"\b(?:DENY|REJECT|LIMIT)\b", ufw):
            return Plan(
                False,
                "UFW 存在限制规则；为保留原有策略，不自动覆盖，请管理员确认本端口的规则顺序。",
            )
        allowed = bool(
            re.search(rf"(?m)^{port}/tcp\s+ALLOW(?: IN)?\s+Anywhere\s*$", ufw)
        )
        return Plan(
            allowed,
            "使用现有 UFW，仅添加本端口入站规则；UFW 自己负责持久化。",
            () if allowed else (("ufw", "allow", f"{port}/tcp"),),
        )
    if firewalld:
        routes = json.loads(
            ops.run(["ip", "-j", "-4", "route", "get", "1.1.1.1"], timeout=5).stdout
        )
        if not routes or not routes[0].get("dev"):
            raise Error("没有可用的默认路由接口")
        dev = routes[0]["dev"]
        zone = ops.run(
            ["firewall-cmd", "--get-zone-of-interface=" + dev], timeout=5
        ).stdout.strip()
        if not zone or zone == "no zone":
            zone = ops.run(
                ["firewall-cmd", "--get-default-zone"], timeout=5
            ).stdout.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", zone):
            raise Error("无法确定接口所属 firewalld zone")
        commands = []
        for persistent in (False, True):
            base = ("firewall-cmd", "--zone=" + zone) + (
                ("--permanent",) if persistent else ()
            )
            result = ops.run(
                [*base, f"--query-port={port}/tcp"], check=False, timeout=5
            )
            if result.returncode not in (0, 1):
                raise Error(result.stdout.strip() or "firewalld 查询失败")
            if result.returncode:
                commands.append((*base, f"--add-port={port}/tcp"))
        return Plan(
            not commands,
            f"接口 {dev} 的 {zone} 区域：分别配置当前规则和永久规则，不重载其它规则。",
            tuple(commands),
        )

    # Install inspection tools, not another firewall manager. nftables services are
    # not enabled here. iptables also lets us inspect legacy rules when available.
    missing = []
    if not shutil.which("nft"):
        missing.append("nftables")
    if not shutil.which("iptables-save"):
        missing.append("iptables")
    if missing:
        return Plan(
            False,
            "补齐发行版 nftables / iptables 检查工具；不启用新防火墙，不清空规则。安装后重新检查。",
            packages(missing),
        )
    rules = json.loads(ops.run(["nft", "--json", "list", "ruleset"], timeout=5).stdout)
    entries = rules["nftables"]
    if not isinstance(entries, list):
        raise Error("无法识别 nftables 规则")
    # Conservative: arbitrary custom rules cannot be safely rewritten by guessing
    # which chains and configuration files their owner uses.
    filtered = any(
        "rule" in item or item.get("chain", {}).get("policy", "accept") != "accept"
        for item in entries
    )
    for tool in ("iptables-save", "iptables-legacy-save"):
        if not shutil.which(tool):
            continue
        output = ops.run([tool], timeout=5).stdout
        filtered |= any(
            line.startswith("-A ")
            or (line.startswith(":") and line.split()[1] not in ("ACCEPT", "-"))
            for line in output.splitlines()
        )
    if filtered:
        return Plan(
            False,
            "存在自定义 nftables / iptables 规则。为保留原策略，需要管理员通过原有规则文件放行目标端口；不自动清空、替换或绕过规则。",
        )
    return Plan(
        True,
        "已检查 nftables 和可用的 iptables 规则：未发现过滤规则或拒绝策略，无需新增防火墙。此项不证明 Windows 或局域网链路已通。",
    )


def configure(port, ops=None):
    """Replan after provisioning, execute native commands sequentially, verify."""
    ops = ops or Operations()
    for attempt in range(4):
        plan = inspect(port, ops)
        print(plan.reason, flush=True)
        if plan.ready:
            return
        if not plan.commands:
            raise Error(plan.reason)
        for command in plan.commands:
            result = ops.run(list(command), timeout=1200)
            if result.stdout:
                print(result.stdout, end="", flush=True)
    raise Error("配置后检查仍未通过；保留已有系统策略，请查看上述输出。")
