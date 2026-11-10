#!/bin/sh
# Provision the management tool only. Never install/load backends or alter their units.
set -eu
[ "$(uname -s)" = Linux ] || { echo 'Linux is required.' >&2; exit 1; }
LLMSM_REPOSITORY=${LLMSM_REPOSITORY:-gdrencn/llm-service-manager}
LLMSM_REF=${LLMSM_REF:-v0.10.5}
[ -n "$LLMSM_REF" ] || { echo "No stable release is available yet. Use the explicitly labelled test installer." >&2; exit 1; }
llmsm_user=${LLMSM_USER:-${SUDO_USER:-$(id -un)}}
id "$llmsm_user" >/dev/null 2>&1 || { echo 'LLMSM_USER must identify an existing account.' >&2; exit 1; }
# sudo is provisioned if missing; su is only a bootstrap fallback, never a credential store.
as_root() {
 if [ "$(id -u)" = 0 ]; then "$@"
 elif command -v sudo >/dev/null 2>&1; then sudo -- "$@"
 elif command -v su >/dev/null 2>&1; then
  llmsm_cmd=
  for llmsm_arg do
   llmsm_quoted=$(printf '%s' "$llmsm_arg" | sed "s/'/'\\\\''/g")
   llmsm_cmd="$llmsm_cmd '$llmsm_quoted'"
  done
  su -c "exec $llmsm_cmd"
 else echo 'Administrator access is required; run as root.' >&2; exit 1
 fi
}

# Deliberately support package-managed apt/dnf systems; never guess another distribution's packages.
llmsm_missing=
for llmsm_program in python3 curl tar gzip sudo bash systemctl systemd-analyze journalctl ip ss; do
 command -v "$llmsm_program" >/dev/null 2>&1 || llmsm_missing="$llmsm_missing $llmsm_program"
done
if [ ! -f /etc/ssl/certs/ca-certificates.crt ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ]; then llmsm_missing="$llmsm_missing ca-certificates"; fi
if [ -n "$llmsm_missing" ]; then
 echo "Provisioning missing tools:$llmsm_missing"
 if command -v apt-get >/dev/null 2>&1; then
  as_root apt-get update
  as_root apt-get install -y python3 ca-certificates curl tar gzip sudo bash systemd coreutils util-linux iproute2
 elif command -v dnf >/dev/null 2>&1; then
  as_root dnf install -y python3 ca-certificates curl tar gzip sudo bash systemd coreutils util-linux iproute
 else echo 'Automatic dependency provisioning supports apt/dnf Linux distributions.' >&2; exit 1
 fi
fi
# Packages cannot replace the running init process or a WSL host GPU driver.
[ -d /run/systemd/system ] || { echo 'systemd is not running. Enable systemd in this Linux/WSL environment and restart it, then rerun.' >&2; exit 1; }
systemctl show-environment >/dev/null 2>&1 || { echo 'The systemd system bus is unavailable.' >&2; exit 1; }
llmsm_python=$(command -v python3)
"$llmsm_python" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required; upgrade the Linux distribution or its Python package"'
llmsm_tmp=$(mktemp -d)
trap 'rm -rf "$llmsm_tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
curl -fsSL "https://github.com/$LLMSM_REPOSITORY/archive/$LLMSM_REF.tar.gz" -o "$llmsm_tmp/source.tar.gz"
mkdir "$llmsm_tmp/source"
tar -xzf "$llmsm_tmp/source.tar.gz" -C "$llmsm_tmp/source" --strip-components=1
for llmsm_file in llm_service_manager/__main__.py inline_ui/__init__.py launcher; do
 [ -f "$llmsm_tmp/source/$llmsm_file" ] || { echo "Invalid project archive: missing $llmsm_file" >&2; exit 1; }
done
llmsm_generation="$(date +%Y%m%d%H%M%S)-$$"
llmsm_release="/opt/llm-service-manager/releases/$llmsm_generation"
as_root install -d -m 755 "$llmsm_release" /usr/local/bin /etc/llm-service-manager
as_root cp -R "$llmsm_tmp/source/llm_service_manager" "$llmsm_tmp/source/inline_ui" "$llmsm_release/"
as_root cp "$llmsm_tmp/source/README.md" "$llmsm_release/"
as_root chown -R root:root "$llmsm_release"
as_root chmod -R a+rX "$llmsm_release"
# Validate the candidate code and initialize tool identity before activating this release.
as_root "$llmsm_python" - "$llmsm_release" "$llmsm_user" <<'PY'
import json,pwd,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from llm_service_manager.config import normalized,validate
from llm_service_manager.config_document import encode
from llm_service_manager.storage import atomic,read_config
from llm_service_manager.network import initialize_port
# Import UI and CLI before activating the candidate; importing runs no operation.
import llm_service_manager.__main__
import llm_service_manager.tui
p=Path('/etc/llm-service-manager/config.json')
if p.exists():read_config(p)
else:
 account=pwd.getpwnam(sys.argv[2])
 cfg=initialize_port(normalized({},home=account.pw_dir,user=account.pw_name))
 validate(cfg)
 atomic(p,json.dumps(encode(cfg),ensure_ascii=False,indent=2)+'\n')
PY
as_root ln -s "$llmsm_release" "/opt/llm-service-manager/.current-$llmsm_generation"
as_root mv -Tf "/opt/llm-service-manager/.current-$llmsm_generation" /opt/llm-service-manager/current
{ printf '#!%s\n' "$llmsm_python"; tail -n +2 "$llmsm_tmp/source/launcher"; } > "$llmsm_tmp/llmsm"
as_root install -m 755 "$llmsm_tmp/llmsm" /usr/local/bin/llmsm
printf '\nllm-service-manager installed. Run: llmsm\n'
