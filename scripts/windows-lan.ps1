#Requires -Version 5.1
<#
One-shot WSL2 NAT setup using Windows-native portproxy and firewall commands.
No scheduled task, daemon, or dependency on an installed llmsm. Rerun after a
WSL IP change. Dot-source to load functions without executing configuration.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Distro,
    [ValidateRange(1, 65535)][int]$Port = 55555,
    [switch]$CheckOnly,
    [switch]$Pause
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Title, [string]$Why) {
    Write-Host "`n$Title" -ForegroundColor Cyan
    Write-Host $Why
}

function Invoke-Native([string]$Program, [string[]]$Arguments) {
    Write-Host ('$ ' + $Program + ' ' + (($Arguments | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ' ')) -ForegroundColor DarkGray
    $output = & $Program @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE): $output" }
    return ($output -join "`n")
}

function Get-ProxyRows([string]$Text) {
    foreach ($line in $Text -split "`r?`n") {
        if ($line -match '^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s*$') {
            [pscustomobject]@{ Listen = $Matches[1]; Port = [int]$Matches[2]; Target = $Matches[3]; TargetPort = [int]$Matches[4] }
        }
    }
}

function Invoke-LanSetup([string]$Distribution, [int]$ServicePort, [bool]$ReadOnly) {
    Write-Step '[1/6] Check WSL networking' 'NAT needs Windows TCP forwarding. Do not change the WSL network mode.'
    $mode = (Invoke-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'wslinfo', '--networking-mode')).Trim()
    if ($mode -ne 'nat') { throw "Expected NAT, got '$mode'. No forwarding configured. Update WSL if wslinfo is unavailable." }
    $routeText = Invoke-Native 'wsl.exe' @('-d', $Distribution, '--exec', 'ip', '-j', '-4', 'route', 'get', '1.1.1.1')
    $routes = @($routeText | ConvertFrom-Json)
    if ($routes.Count -eq 0) { throw 'No default IPv4 route in the selected distribution.' }
    $ip = [string]$routes[0].prefsrc
    $address = $null
    if (-not [Net.IPAddress]::TryParse($ip, [ref]$address) -or $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or [Net.IPAddress]::IsLoopback($address) -or $ip -eq '0.0.0.0') {
        throw "Invalid WSL IPv4: $ip"
    }
    Write-Host "WSL $Distribution -> $ip`:$ServicePort"

    Write-Step '[2/6] Check the LAN and backend' 'Allow only Private networks and LocalSubnet. Keep existing network profiles unchanged.'
    Write-Host '$ Get-NetConnectionProfile; Get-NetIPConfiguration' -ForegroundColor DarkGray
    $profiles = @(Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Private')
    $lan = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and ($profiles.InterfaceIndex -contains $_.InterfaceIndex) })
    if ($lan.Count -eq 0) { throw 'No Private LAN with an IPv4 gateway. Mark your trusted LAN Private in Windows settings, then rerun.' }
    Write-Host ('$ Test-NetConnection -ComputerName ' + $ip + ' -Port ' + $ServicePort + ' -InformationLevel Quiet') -ForegroundColor DarkGray
    if (-not (Test-NetConnection -ComputerName $ip -Port $ServicePort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        throw 'Windows cannot reach the Linux service. Apply the Linux listening/firewall configuration and start the model first.'
    }

    Write-Step '[3/6] Check Windows port ownership' 'Do not replace another program or another distribution forwarding rule.'
    $proxy = @(Get-ProxyRows (Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', 'v4tov4')))
    $existing = @($proxy | Where-Object { $_.Port -eq $ServicePort })
    $name = "llmsm-LAN-TCP-$ServicePort"
    # The description is a native rule property, retained without llmsm installed.
    $owner = "WSL NAT: $Distribution"
    Write-Host ('$ Get-NetFirewallRule; Get-NetTCPConnection -State Listen # TCP ' + $ServicePort) -ForegroundColor DarkGray
    $owned = @(Get-NetFirewallRule | Where-Object Name -eq $name)
    if ($owned.Count -gt 0 -and @($owned | Where-Object Description -ne $owner).Count -gt 0) {
        throw "Firewall rule $name already exists with another owner; inspect it before continuing."
    }
    if (@($existing | Where-Object { $_.Listen -ne '0.0.0.0' -or $_.TargetPort -ne $ServicePort -or ($_.Target -ne $ip -and $owned.Count -ne 1) }).Count -gt 0) {
        throw 'A different portproxy already uses this port. Choose another port or resolve the conflict manually.'
    }
    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq $ServicePort)
    if ($existing.Count -eq 0 -and $listeners.Count -gt 0) { throw 'Windows TCP port is occupied. Change the service port and rerun.' }
    $excluded = Invoke-Native 'netsh.exe' @('interface', 'ipv4', 'show', 'excludedportrange', 'protocol=tcp')
    foreach ($line in $excluded -split "`r?`n") {
        if ($line -match '^\s*(\d+)\s+(\d+)\s*\*?\s*$' -and $ServicePort -ge [int]$Matches[1] -and $ServicePort -le [int]$Matches[2] -and $existing.Count -eq 0) {
            throw 'Windows reserves this port. Choose another service port.'
        }
    }

    Write-Step '[4/6] Configure native TCP forwarding' 'IP Helper runs the Windows portproxy. The rule persists; rerun this script if WSL gets a new IP.'
    Write-Host '$ Get-Service iphlpsvc' -ForegroundColor DarkGray
    $helper = Get-Service iphlpsvc
    if ($helper.StartType -eq 'Disabled') { throw 'IP Helper is disabled by system policy. Enable it deliberately before using portproxy.' }
    $verb = if ($existing.Count -gt 0) { 'set' } else { 'add' }
    $args = @('interface', 'portproxy', $verb, 'v4tov4', 'listenaddress=0.0.0.0', "listenport=$ServicePort", "connectaddress=$ip", "connectport=$ServicePort")
    if ($ReadOnly) {
        Write-Host ('Would run: Start-Service iphlpsvc; netsh.exe ' + ($args -join ' '))
    } else {
        if ($helper.Status -ne 'Running') {
            Write-Host '$ Start-Service iphlpsvc' -ForegroundColor DarkGray
            Start-Service iphlpsvc
        }
        Invoke-Native 'netsh.exe' $args | Write-Host
    }

    Write-Step '[5/6] Configure the native firewall rule' 'Only this named inbound TCP rule is changed. Other rules, default policies and network profiles stay intact.'
    $rule = @{ Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = $ServicePort; Profile = 'Private'; RemoteAddress = 'LocalSubnet'; Enabled = 'True' }
    $operation = if ($owned.Count -gt 0) { 'Set-NetFirewallRule' } else { 'New-NetFirewallRule' }
    Write-Host ('$ ' + $operation + " -Name '$name' -Description '$owner' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ServicePort -Profile Private -RemoteAddress LocalSubnet -Enabled True") -ForegroundColor DarkGray
    if (-not $ReadOnly) {
        if ($owned.Count -gt 0) { Set-NetFirewallRule -Name $name -Description $owner @rule | Out-Null }
        else { New-NetFirewallRule -Name $name -DisplayName "LLM LAN TCP $ServicePort" -Description $owner @rule | Out-Null }
    }

    Write-Step '[6/6] Verify and show client addresses' 'A local check cannot prove access from another computer; test the URL there as well.'
    if ($ReadOnly) { Write-Host 'Check-only: no Windows configuration was changed.'; return }
    $verified = @(Get-ProxyRows (Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', 'v4tov4')) | Where-Object { $_.Listen -eq '0.0.0.0' -and $_.Port -eq $ServicePort -and $_.Target -eq $ip -and $_.TargetPort -eq $ServicePort })
    if ($verified.Count -ne 1) { throw 'Forwarding verification failed; inspect the displayed commands.' }
    Write-Host ('$ Get-NetFirewallRule -PolicyStore ActiveStore -Name ' + $name + ' # verify rule, port and address filters') -ForegroundColor DarkGray
    $active = Get-NetFirewallRule -PolicyStore ActiveStore -Name $name
    $ports = $active | Get-NetFirewallPortFilter
    $remote = $active | Get-NetFirewallAddressFilter
    if ([string]$active.Enabled -ne 'True' -or [string]$active.Direction -ne 'Inbound' -or [string]$active.Action -ne 'Allow' -or [string]$active.Profile -ne 'Private' -or [string]$ports.Protocol -notin @('TCP', '6') -or [string]$ports.LocalPort -ne [string]$ServicePort -or [string]$remote.RemoteAddress -ne 'LocalSubnet') {
        throw 'Firewall verification failed. Domain policy or other rules may override local settings.'
    }
    if (-not (Test-NetConnection -ComputerName '127.0.0.1' -Port $ServicePort -InformationLevel Quiet -WarningAction SilentlyContinue)) { throw 'The forwarding listener is not reachable yet. Check IP Helper and port reservations.' }
    foreach ($item in $lan) {
        foreach ($local in $item.IPv4Address) {
            Write-Host ("Client Base URL: http://" + $local.IPAddress + ":$ServicePort/v1") -ForegroundColor Green
            Write-Host ("Verify on another PC: curl.exe http://" + $local.IPAddress + ":$ServicePort/v1/models")
        }
    }
    Write-Host 'Done. No llmsm process or scheduled task is needed. WSL and the model service must remain running.'
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $admin) {
            if (-not $PSCommandPath) { throw 'Download this script to a file before running it so it can request elevation.' }
            Write-Step 'Request Windows administrator approval' 'Windows requires UAC approval for firewall and portproxy configuration. No need to open an administrator terminal manually.'
            $call = "& '" + $PSCommandPath.Replace("'", "''") + "' -Distro '" + $Distro.Replace("'", "''") + "' -Port $Port -Pause"
            if ($CheckOnly) { $call += ' -CheckOnly' }
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($call + '; exit $LASTEXITCODE'))
            $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Wait -PassThru
            if ($child.ExitCode -ne 0) { throw "Elevated setup failed (exit $($child.ExitCode)). See the elevated window output." }
        } else {
            Invoke-LanSetup $Distro $Port ([bool]$CheckOnly)
        }
        if ($Pause) { Read-Host 'Press Enter to close' | Out-Null }
        exit 0
    } catch {
        Write-Host ("Setup stopped: " + $_.Exception.Message) -ForegroundColor Red
        Write-Host 'Completed native changes remain in place. Fix the reported problem and rerun; no unrelated settings were reset.'
        if ($Pause) { Read-Host 'Press Enter to close' | Out-Null }
        exit 1
    }
}
