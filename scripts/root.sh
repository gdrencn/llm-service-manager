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
