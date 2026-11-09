"""One-shot local server setup. A successful TCP check requires no mutation."""

import copy
import ipaddress
import socket

from .config import Error, value
from .linux_lan import configure
from .manager import switch
from .storage import locked, read_snapshot


def endpoint(port):
    """Route selection sends no datagram and needs no external command/package."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("1.1.1.1", 9))
            host = route.getsockname()[0]
        address = ipaddress.IPv4Address(host)
        if address.is_loopback or address.is_unspecified:
            return None
        return host, port
    except OSError:
        return None


def reachable(target):
    if target is None:
        return False
    try:
        with socket.create_connection(target, timeout=2):
            return True
    except OSError:
        return False


def complete():
    c, revision = read_snapshot()
    port = value(c, "port")
    target = endpoint(port)
    print(f"检查本机非回环 IPv4 的 TCP {port} 端口…", flush=True)
    if reachable(target):
        print(
            f"服务器端设置已完成：{target[0]}:{port} 可连接。未修改任何配置。",
            flush=True,
        )
        return
    if target is None:
        raise Error(
            "没有可用的本机 IPv4 路由。请先连接网络；工具不会擅自修改 IP、路由或 DNS。"
        )
    selected = c.get("target")
    if not selected:
        raise Error(
            "服务端口不可连接，且尚未选择模型。请先在“切换底座/模型”中选择已安装模型。"
        )
    # Reuse the same validated, transactional backend operation as the main menu.
    # Its port check refuses an unknown occupying program, and failures roll back.
    candidate = copy.deepcopy(c)
    candidate["settings"]["host"]["value"] = "0.0.0.0"
    candidate["model_selection"][selected["backend"]] = selected["model"]
    print("端口不可连接；应用非回环监听并启动选定模型。", flush=True)
    switch(candidate, selected["backend"], revision)
    current, revision = read_snapshot()
    target = endpoint(value(current, "port"))
    if not reachable(target):
        print("继续检查 Linux 防火墙，并按需补齐发行版工具及端口规则。", flush=True)
        with locked():
            latest, current_revision = read_snapshot()
            if current_revision != revision:
                raise Error("配置已被其它操作修改，请重新执行服务器端设置。")
            configure(value(latest, "port"))
        target = endpoint(value(current, "port"))
        if not reachable(target):
            raise Error(
                "配置后端口仍不可连接。请检查上方错误及本机网络策略；未宣告设置成功。"
            )
    print(f"服务器端设置已完成：{target[0]}:{target[1]} 可连接。", flush=True)
