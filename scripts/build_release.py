"""Build a source release with paired tests and checksums from a clean Git tree."""

import gzip
import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def git(*args):
    return subprocess.check_output(["git", *args], cwd=root)


if git("status", "--porcelain").strip():
    raise SystemExit("Commit changes before building a release")
version = json.loads((root / "release.json").read_text())
commit = git("rev-parse", "HEAD").decode().strip()
out = root / "dist"
out.mkdir(exist_ok=True)
name = f"llm-service-manager-{version['version']}-test.tar.gz"
blob = git(
    "archive",
    "--format=tar",
    f"--prefix=llm-service-manager-{version['version']}/",
    "HEAD",
)
(out / name).write_bytes(gzip.compress(blob, mtime=0))
(out / "windows-lan.ps1").write_bytes((root / "scripts/windows-lan.ps1").read_bytes())
(out / "RELEASE.json").write_text(
    json.dumps(dict(version, commit=commit, archive=name), indent=2) + "\n"
)
assets = [out / name, out / "windows-lan.ps1", out / "RELEASE.json"]
(out / "SHA256SUMS").write_text(
    "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets)
)
print(out)
