"""Explicit stable updates; no timers, backend operations or configuration writes."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from . import __version__
from .config import Error
from .execution import Operations, report_outcome
from .paths import CONFIG, STATE
from .presentation import t
from .storage import locked

REPOSITORY = "gdrencn/llm-service-manager"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ROOT = Path("/opt/llm-service-manager")
MAX_ARCHIVE = 64 * 1024 * 1024


def version(text):
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", text):
        raise Error(t("发布版本格式无效", "Invalid release version"))
    return tuple(map(int, text.split(".")))


def download(url, destination, limit):
    """Bounded public download; honor proxies, unlike local backend API calls."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "llmsm/" + __version__}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        with Path(destination).open("wb") as out:
            size = 0
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise Error(
                        t("更新文件超出大小限制", "Update file exceeds size limit")
                    )
                out.write(chunk)


def asset_url(release, name):
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise Error("Invalid release assets")
    matches = [x for x in assets if isinstance(x, dict) and x.get("name") == name]
    if len(matches) != 1:
        raise Error(t("缺少唯一的发布文件：", "Missing unique release asset: ") + name)
    url = matches[0].get("browser_download_url")
    if not isinstance(url, str):
        raise Error("Invalid release asset URL")
    parsed = urllib.parse.urlsplit(url)
    expected = f"/{REPOSITORY}/releases/download/{release['tag_name']}/{name}"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or urllib.parse.unquote(parsed.path) != expected
        or parsed.query
        or parsed.fragment
    ):
        raise Error("Unexpected release asset URL")
    return url


def latest(directory, fetch=download):
    metadata = directory / "latest.json"
    try:
        fetch(LATEST, metadata, 1024 * 1024)
    except urllib.error.HTTPError as ex:
        ex.close()
        if ex.code == 404:
            return None
        raise
    release = json.loads(metadata.read_bytes())
    if not isinstance(release, dict):
        raise Error("Invalid release metadata")
    tag = release.get("tag_name", "")
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(tag, str)
        or not tag.startswith("stable/")
    ):
        raise Error(
            t(
                "最新发布不是有效的 stable 版本",
                "Latest release is not a valid stable release",
            )
        )
    number = tag.removeprefix("stable/")
    version(number)
    return release, number


def unpack(archive, destination):
    """Extract product files only; reject links, traversal and oversized bundles."""
    seen = set()
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise Error("Unsafe release archive path")
            if not (member.isdir() or member.isfile()):
                raise Error("Release archive contains a link or special file")
            total += member.size
            if total > 128 * 1024 * 1024 or len(seen) > 10000:
                raise Error("Release archive exceeds extraction limits")
            if path in seen:
                raise Error("Duplicate release archive path")
            seen.add(path)
            relative = PurePosixPath(*path.parts[1:])
            if not relative.parts or relative.parts[0] not in (
                "llm_service_manager",
                "inline_ui",
                "README.md",
            ):
                continue
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as data, target.open("xb") as out:
                    shutil.copyfileobj(data, out)
                target.chmod(0o644)
    for name in (
        "llm_service_manager/__main__.py",
        "inline_ui/__init__.py",
        "README.md",
    ):
        if not (destination / name).is_file():
            raise Error("Incomplete release archive: " + name)
    # The launcher imports before requesting sudo. A restrictive root umask must
    # not make the next invocation inaccessible to the ordinary installing user.
    for directory in destination.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o755)


def validate_candidate(candidate, number, config):
    # Isolated child ensures imports come from the candidate, not the running UI.
    script = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from llm_service_manager import __version__
assert __version__ == sys.argv[2], 'Release version mismatch'
import llm_service_manager.__main__
import llm_service_manager.tui
from llm_service_manager.storage import read_config
p = Path(sys.argv[3])
if p.exists(): read_config(p)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(candidate), number, str(config)],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode:
        raise Error(
            t(
                "新版代码或配置兼容性检查失败：\n",
                "Candidate code/configuration check failed:\n",
            )
            + result.stdout[-4000:]
        )


def activate(archive, number, *, root=ROOT, config=CONFIG, validate=validate_candidate):
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise Error(
            t(
                "current 不是符号链接，拒绝覆盖",
                "Refusing to replace a non-symlink current directory",
            )
        )
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    releases.chmod(0o755)
    candidate = Path(tempfile.mkdtemp(prefix=number + "-", dir=releases))
    link = root / (".current-" + candidate.name)

    def activated():
        return current.is_symlink() and current.resolve() == candidate

    try:
        try:
            unpack(archive, candidate)
        except tarfile.TarError as ex:
            raise Error(t("更新包格式无效", "Invalid update archive")) from ex
        candidate.chmod(0o755)
        validate(candidate, number, config)
        link.symlink_to(candidate)
        os.replace(link, current)
    except BaseException:
        # The atomic replace is the commit point, even if interrupted immediately
        # afterwards. Never remove an already active candidate or continue old UI.
        if not activated():
            raise
    finally:
        link.unlink(missing_ok=True)
        if not activated():
            shutil.rmtree(candidate)


def check_and_update(
    *,
    current=__version__,
    state=STATE,
    root=ROOT,
    config=CONFIG,
    fetch=download,
    install=activate,
):
    """Return True only when activated; callers must exit the old process then."""
    ops = Operations(state)
    with locked(state):
        if (Path(state) / "pending.json").exists():
            raise Error(
                t(
                    "请先恢复未完成的配置操作，再更新工具",
                    "Recover the pending configuration operation before updating",
                )
            )
        print(t("正在检查 stable 更新…", "Checking for stable updates…"), flush=True)
        with tempfile.TemporaryDirectory(prefix="llmsm-update-") as tmp:
            directory = Path(tmp)
            result = latest(directory, fetch)
            if result is None:
                print(t("尚无 stable 发布。", "No stable release is available."))
                return False
            release, number = result
            if version(number) <= version(current):
                print(
                    t("当前版本无需更新：", "No update needed for current version: ")
                    + current
                )
                return False
            print(
                t("发现新版，开始更新：", "New version found; updating: ")
                + current
                + " → "
                + number,
                flush=True,
            )
            # Stable promotions reuse the tested archive byte-for-byte.
            names = (
                f"llm-service-manager-{number}-test.tar.gz",
                f"llm-service-manager-{number}.tar.gz",
            )
            available = release.get("assets")
            if not isinstance(available, list):
                raise Error("Invalid release assets")
            matching = [
                n
                for n in names
                if any(isinstance(a, dict) and a.get("name") == n for a in available)
            ]
            if len(matching) != 1:
                raise Error("Missing unique product archive")
            name = matching[0]
            sums = directory / "SHA256SUMS"
            fetch(asset_url(release, "SHA256SUMS"), sums, 1024 * 1024)
            hashes = [
                line.split()[0]
                for line in sums.read_text().splitlines()
                if len(line.split()) == 2 and line.split()[1] == name
            ]
            if len(hashes) != 1 or not re.fullmatch(r"[0-9a-f]{64}", hashes[0]):
                raise Error("Missing unique archive checksum")
            archive = directory / name
            print(
                t("正在下载并校验更新包…", "Downloading and verifying update…"),
                flush=True,
            )
            fetch(asset_url(release, name), archive, MAX_ARCHIVE)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != hashes[0]:
                raise Error(
                    t(
                        "更新包校验失败，旧版本保持不变",
                        "Update checksum failed; old version unchanged",
                    )
                )
            ops.event(
                "tool-update-start",
                previous=current,
                version=number,
                tag=release["tag_name"],
            )
            try:
                print(
                    t(
                        "正在检查新版代码和配置兼容性…",
                        "Validating candidate code and configuration…",
                    ),
                    flush=True,
                )
                install(archive, number, root=root, config=config)
            except BaseException as ex:
                report_outcome(ops, "tool-update-failed", error=str(ex))
                raise
            report_outcome(ops, "tool-update-complete", version=number)
            print(
                t(
                    "更新完成。返回后退出 llmsm，请重新运行。模型服务未作改动。",
                    "Updated. Returning will exit llmsm; run it again. Model services are unchanged.",
                )
            )
            return True
