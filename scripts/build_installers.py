"""Generate both standalone installers and the copyable README entry from shared code.
--check only compares text; it never executes an installer.
"""

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
check = "--check" in sys.argv
helper = (root / "scripts/root.sh").read_text().rstrip()
sys.path.insert(0, str(root))
from llm_service_manager import __version__  # noqa: E402 - local source, no installed package required

for name, template, default_ref, install_path in (
    ("install.sh", "install.sh.in", "", "install.sh"),
    ("test/install.sh", "install.sh.in", "v" + __version__, "test/install.sh"),
    ("bootstrap.sh", "bootstrap.sh.in", "main", "install.sh"),
    ("test/bootstrap.sh", "bootstrap.sh.in", "v" + __version__, "test/install.sh"),
):
    content = (
        (root / "scripts" / template)
        .read_text()
        .replace("@ROOT_HELPER@", helper)
        .replace("@DEFAULT_REF@", default_ref)
        .replace("@BOOTSTRAP_REF@", default_ref)
        .replace("@INSTALL_PATH@", install_path)
    )
    path = root / name
    if check:
        if not path.exists() or path.read_text() != content:
            raise SystemExit(f"Out of date: {name}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
p = root / "README.md"
s = p.read_text()
begin = "<!-- bootstrap:start -->"
end = "<!-- bootstrap:end -->"
if begin in s:
    a = s.index(begin)
    b = s.index(end, a) + len(end)
    block = (
        begin
        + "\n```sh\nsh <<'LLMSM_BOOTSTRAP'\n"
        + (root / "test/bootstrap.sh").read_text()
        + "LLMSM_BOOTSTRAP\n```\n"
        + end
    )
    updated = s[:a] + block + s[b:]
    if check:
        if updated != s:
            raise SystemExit("README bootstrap is out of date")
    else:
        p.write_text(updated)
