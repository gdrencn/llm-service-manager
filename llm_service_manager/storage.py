"""Atomic persistence and process locks. No service operations."""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .config import Error, normalized, validate
from .paths import CONFIG, STATE


def atomic(path, data, *, metadata=None):
    path = Path(path)
    if path.is_symlink():
        raise Error("拒绝覆盖符号链接配置：" + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
            f.flush()
            attributes = metadata
            if attributes is None and path.exists():
                st = path.stat()
                attributes = {
                    "mode": st.st_mode & 0o7777,
                    "uid": st.st_uid,
                    "gid": st.st_gid,
                }
            if attributes:
                os.fchown(f.fileno(), attributes["uid"], attributes["gid"])
            os.fchmod(f.fileno(), attributes["mode"] if attributes else 0o644)
            os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def read_snapshot(path=CONFIG):
    """Parse and hash the same bytes, so concurrent edits cannot acquire the wrong revision."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        data = None
    except OSError as ex:
        raise Error("无法读取配置：" + str(ex)) from ex
    try:
        raw = {} if data is None else json.loads(data)
    except (ValueError, UnicodeError) as ex:
        raise Error("配置 JSON 无效：" + str(ex)) from ex
    c = normalized(raw)
    validate(c)
    if "value" not in raw.get("settings", {}).get("port", {}):
        from .network import initialize_port

        initialize_port(c)
    return c, "<absent>" if data is None else hashlib.sha256(data).hexdigest()


def read_config(path=CONFIG):
    return read_snapshot(path)[0]


def revision(path=CONFIG):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return "<absent>"


@contextmanager
def locked(state=STATE):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    with (state / "lock").open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error("另一个切换/配置操作正在运行")
        yield
