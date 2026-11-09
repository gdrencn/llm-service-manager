"""Render native systemd syntax; no shell wrapper or tool runtime dependency."""

from ..paths import UNITS


def unit_quote(s, expand_dollar=True):
    # systemd ExecStart is not shell syntax; percent and dollar expansion must be escaped.
    result = (
        '"'
        + str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$" if expand_dollar else "$")
        + '"'
    )

    return result


def service(c, b, args, cwd=None, extra=""):
    rt = c["runtime"]
    conflicts = " ".join(u for key, u in UNITS.items() if key != b)
    return (
        "# Managed by llmsm\n[Unit]\nDescription=LLM "
        + b
        + "\nAfter=network-online.target\nConflicts="
        + conflicts
        + "\n\n[Service]\nUser="
        + rt["user"]
        + "\nWorkingDirectory="
        + str(cwd or rt["home"]).replace("%", "%%")
        + "\nEnvironment="
        + unit_quote("HOME=" + rt["home"], expand_dollar=False)
        + "\nExecStart="
        + " ".join(map(unit_quote, args))
        + "\nRestart=no\nTimeoutStartSec=900\nTimeoutStopSec=60\n"
        + extra
        + "\n[Install]\nWantedBy=multi-user.target\n"
    )
