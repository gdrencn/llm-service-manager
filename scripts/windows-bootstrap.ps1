# Download the matching version and run interactively in the existing console.
$llmsmScript = Join-Path $env:TEMP ('llmsm-' + [guid]::NewGuid().ToString() + '.ps1')
try {
    Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.3/scripts/windows-lan.ps1' -OutFile $llmsmScript -ErrorAction Stop
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $llmsmScript
    if ($LASTEXITCODE -ne 0) { throw 'LAN setup did not complete. See the error above.' }
} finally {
    Remove-Item -LiteralPath $llmsmScript -ErrorAction SilentlyContinue
}
