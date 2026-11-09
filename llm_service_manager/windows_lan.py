"""Copyable standalone Windows setup entry; no Windows daemon installed."""

SCRIPT_URL = "https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.0/scripts/windows-lan.ps1"


def command(distro, port):
    quoted = "'" + distro.replace("'", "''") + "'"
    return (
        "$p=Join-Path $env:TEMP ([guid]::NewGuid().ToString()+'.ps1'); "
        f"try {{ Invoke-WebRequest -UseBasicParsing '{SCRIPT_URL}' -OutFile $p -ErrorAction Stop; "
        f"& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Distro {quoted} -Port {int(port)}; "
        "if ($LASTEXITCODE -ne 0) { throw 'LAN setup failed' } } finally { Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue }"
    )
