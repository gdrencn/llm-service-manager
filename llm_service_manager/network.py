"""Local socket checks and first-use defaults; never change a firewall."""

import errno
import socket

from .config import SPECS, Error, value

DEFAULT_PORT = SPECS["port"][1]
DEFAULT_HOST = SPECS["host"][1]


def check_bind(host, port, *, reuse=False):
    seen = set()
    for family, kind, protocol, _, endpoint in socket.getaddrinfo(
        host.strip("[]"), port, type=socket.SOCK_STREAM
    ):
        if (family, endpoint) in seen:
            continue
        seen.add((family, endpoint))
        with socket.socket(family, kind, protocol) as probe:
            if reuse:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(endpoint)


def free_port(host=DEFAULT_HOST, start=DEFAULT_PORT, end=65535):
    if not 1 <= start <= end <= 65535:
        raise Error("无效的端口探测范围")
    for port in range(start, end + 1):
        try:
            check_bind(host, port)
        except OSError as ex:
            if ex.errno == errno.EADDRINUSE:
                continue
            raise Error("无法探测本地端口：" + str(ex)) from ex
        return port
    raise Error(f"{start}–{end} 没有可用的本地 TCP 端口")


def initialize_port(c):
    """Only for a missing explicit port. Caller decides when to save."""
    port = free_port(value(c, "host"))
    c["settings"]["port"]["value"] = port
    c["initial_port"] = {"default": DEFAULT_PORT, "selected": port}
    return c


def port_adjustment(c):
    note = c.get("initial_port")
    if (
        isinstance(note, dict)
        and note.get("default") == DEFAULT_PORT
        and type(note.get("selected")) is int
        and DEFAULT_PORT < note["selected"] <= 65535
        and value(c, "port") == note["selected"]
    ):
        return note["selected"]
    return None
