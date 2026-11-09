"""Retire previous generated paths inside the normal reversible apply transaction."""

from pathlib import Path

from .config import Error
from .paths import LEGACY_UNITS, OLLAMA_DROPIN, OLLAMA_MODELFILE, STRATA_CONFIG, UNITS

LEGACY_FILES = {
    "/etc/strata/llmsm.json": STRATA_CONFIG,
    "/etc/ollama/llmsm.Modelfile": OLLAMA_MODELFILE,
    "/etc/systemd/system/ollama.service.d/llm.conf": OLLAMA_DROPIN,
    **{
        "/etc/systemd/system/" + old: "/etc/systemd/system/" + UNITS[b]
        for b, old in LEGACY_UNITS.items()
    },
}


def retired_files(files):
    retired = {}
    for old, new in LEGACY_FILES.items():
        path = Path(old)
        if new not in files or not (path.exists() or path.is_symlink()):
            continue
        if path.is_symlink():
            raise Error("拒绝迁移符号链接配置：" + old)
        if old.endswith((".service", ".conf")):
            if not path.read_text().startswith("# Managed by llmsm\n"):
                raise Error("旧服务配置无法确认由 llmsm 生成，未修改：" + old)
        if Path(new).exists():
            raise Error("新旧配置同时存在，请先确认应保留的配置：" + old + " / " + new)
        retired[old] = None
    return retired


def remove(ops, path):
    ops.event("remove", path=str(path))
    Path(path).unlink(missing_ok=True)
