#!/bin/sh
# Also embedded verbatim in README.md. Only obtains the official project installer.
set -eu
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

if ! command -v curl >/dev/null 2>&1 || { [ ! -f /etc/ssl/certs/ca-certificates.crt ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ]; }; then
 if command -v apt-get >/dev/null 2>&1; then
  as_root apt-get update
  as_root apt-get install -y ca-certificates curl
 elif command -v dnf >/dev/null 2>&1; then as_root dnf install -y ca-certificates curl
 else echo 'No supported package manager (apt/dnf). Cannot provision curl automatically.' >&2; exit 1
 fi
fi
llmsm_bootstrap=$(mktemp)
trap 'rm -f "$llmsm_bootstrap"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
curl -fsSL https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.1/test/install.sh -o "$llmsm_bootstrap"
sh "$llmsm_bootstrap"
