#Requires -Version 5.1
<#
One-shot WSL2 NAT setup using Windows-native portproxy and firewall commands.
No scheduled task, daemon, or dependency on an installed llmsm. Rerun after a
WSL IP change. Dot-source to load functions without executing configuration.
#>
[CmdletBinding()]
param([switch]$CheckOnly, [string]$AdminTarget)

$ErrorActionPreference = 'Stop'
function Write-Step([string]$Title, [string]$Why) {
    Write-Host "`n$Title" -ForegroundColor Cyan
    Write-Host $Why
}

function Invoke-Native([string]$Program, [string[]]$Arguments) {
    Write-Host ('$ ' + $Program + ' ' + (($Arguments | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ' ')) -ForegroundColor DarkGray
    $isWsl = [IO.Path]::GetFileName($Program) -eq 'wsl.exe'
    $oldUtf8 = $env:WSL_UTF8
    $oldEncoding = [Console]::OutputEncoding
    try {
        # Microsoft WSL's own diagnostics use this pair to capture readable output.
        if ($isWsl) { $env:WSL_UTF8 = '1'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) }
        $ErrorActionPreference = 'Continue'
        $output = & $Program @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        if ($isWsl) { $env:WSL_UTF8 = $oldUtf8; [Console]::OutputEncoding = $oldEncoding }
    }
    $text = ($output | ForEach-Object { [string]$_ }) -join "`n"
    if ($code -ne 0) { throw "$Program failed ($code): $text" }
    return $text
}

function Select-Target {
    $names = @((Invoke-Native 'wsl.exe' @('--list', '--quiet')) -split "`r?`n" | ForEach-Object { $_.Trim().Trim([char]0) } | Where-Object { $_ })
    if ($names.Count -eq 0) { throw 'No WSL distributions are registered for the current Windows user.' }
    Write-Step 'Select the WSL server' 'Choose the distribution running your model service. Enter q to cancel.'
    for ($i=0; $i -lt $names.Count; $i++) { Write-Host ("{0}. {1}" -f ($i+1), $names[$i]) }
    while ($true) {
        $answer = Read-Host 'Distribution number [1]'
        if ($answer -eq 'q') { return $null }
        if (-not $answer) { $answer = '1' }
        $number = 0
        if ([int]::TryParse($answer, [ref]$number) -and $number -ge 1 -and $number -le $names.Count) { break }
        Write-Host 'Enter a number from the list.' -ForegroundColor Yellow
    }
    $name = $names[$number-1]
    while ($true) {
        $answer = Read-Host 'Service TCP port [55555] (q to cancel)'
        if ($answer -eq 'q') { return $null }
        if (-not $answer) { $answer = '55555' }
        $portNumber = 0
        if ([int]::TryParse($answer, [ref]$portNumber) -and $portNumber -ge 1 -and $portNumber -le 65535) { break }
        Write-Host 'Enter a TCP port from 1 to 65535.' -ForegroundColor Yellow
    }
    return @{distro=$name; port=$portNumber}
}

function Resolve-WslTarget([string]$Distribution, [int]$ServicePort) {
    Write-Step '[1/6] Check WSL networking' 'Read the selected distribution as the original Windows user, before elevation.'
    $mode = (Invoke-Native 'wsl.exe' @('-d', $Distribution, '--cd', '/', '--exec', 'wslinfo', '--networking-mode')).Trim()
    if ($mode -ne 'nat') { throw "Expected NAT, got '$mode'. No forwarding configured." }
    $routeText = Invoke-Native 'wsl.exe' @('-d', $Distribution, '--cd', '/', '--exec', 'ip', '-j', '-4', 'route', 'get', '1.1.1.1')
    $routes = @($routeText | ConvertFrom-Json)
    if ($routes.Count -eq 0) { throw 'No default IPv4 route in the selected distribution.' }
    $ip = [string]$routes[0].prefsrc
    Assert-Target $Distribution $ServicePort $ip
    Write-Host "WSL $Distribution -> $ip`:$ServicePort"
    return $ip
}

function Assert-Target([string]$Distribution, [int]$ServicePort, [string]$IP) {
    if ([string]::IsNullOrWhiteSpace($Distribution) -or $Distribution -match '[<>\r\n]' -or $ServicePort -lt 1 -or $ServicePort -gt 65535) { throw 'Invalid distribution or TCP port.' }
    $address = $null
    if (-not [Net.IPAddress]::TryParse($IP, [ref]$address) -or $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or [Net.IPAddress]::IsLoopback($address) -or $IP -eq '0.0.0.0') { throw "Invalid WSL IPv4: $IP" }
}

function Get-ProxyRows([string]$Text) {
    foreach ($line in $Text -split "`r?`n") {
        if ($line -match '^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s*$') {
            [pscustomobject]@{ Listen = $Matches[1]; Port = [int]$Matches[2]; Target = $Matches[3]; TargetPort = [int]$Matches[4] }
        }
    }
}

function Invoke-LanSetup([string]$Distribution, [int]$ServicePort, [bool]$ReadOnly, [string]$IP) {
    # The elevated worker never opens WSL under a different Windows identity.
    Assert-Target $Distribution $ServicePort $IP
    $ip = $IP
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

function Test-Administrator {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-InlineSudo {
    $sudo = Join-Path $env:SystemRoot 'System32\sudo.exe'
    if (-not (Test-Path -LiteralPath $sudo)) {
        throw 'Microsoft Sudo for Windows is missing. Windows 11 24H2 or later is required. Update Windows, enable sudo in Settings > System > Advanced, select Inline, then rerun this script.'
    }
    $settings = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Sudo' -ErrorAction SilentlyContinue
    $policy = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Sudo' -ErrorAction SilentlyContinue
    if ($null -ne $policy.Enabled -and $policy.Enabled -lt 3) {
        throw 'Windows policy restricts sudo Inline mode. Ask your administrator to allow Inline mode, then run sudo config --enable normal in administrator PowerShell and rerun this script.'
    }
    if ($settings.Enabled -ne 3) {
        $reason = if ($null -eq $settings.Enabled -or $settings.Enabled -eq 0) { 'Microsoft sudo is not enabled.' } else { 'Microsoft sudo is enabled, but is not configured for Inline mode.' }
        throw "$reason`nOpen PowerShell as administrator and run:`n`n    sudo config --enable normal`n`nThen return to ordinary PowerShell and rerun this script."
    }
    return $sudo
}

function Require-Environment {
    $sudo = Get-InlineSudo
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is not installed. Install WSL2 and prepare the Linux model service, then rerun this script.' }
    return $sudo
}

function Invoke-Setup([bool]$ReadOnly, [string]$ScriptPath) {
    Write-Step 'Check environment' 'Requires WSL and Microsoft Sudo for Windows enabled in Inline mode.'
    $sudo = Require-Environment
    $target = Select-Target
    if ($null -eq $target) { Write-Host 'Cancelled; no changes made.'; return }
    $ip = Resolve-WslTarget $target.distro $target.port
    $payload = @{distro=$target.distro; port=$target.port; ip=$ip; checkOnly=$ReadOnly} | ConvertTo-Json -Compress
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
    Write-Step 'Configure Windows through sudo' 'Approve UAC if prompted. Commands, verification and results stay in this terminal.'
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $sudo --inline $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ScriptPath -AdminTarget $encoded
    if ($LASTEXITCODE -ne 0) { throw 'Configuration failed; see the error above. No alternative elevation or automatic retry.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if ($AdminTarget) {
            if (-not (Test-Administrator)) { throw 'The configuration command requires Windows administrator privileges.' }
            $target = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($AdminTarget)) | ConvertFrom-Json
            Invoke-LanSetup ([string]$target.distro) ([int]$target.port) ([bool]$target.checkOnly) ([string]$target.ip)
        } else { Invoke-Setup ([bool]$CheckOnly) $PSCommandPath }
    } catch {
        Write-Host ('Setup stopped: ' + $_.Exception.Message) -ForegroundColor Red
        Write-Host 'Stopped without proceeding to later steps. Follow the guidance above, then rerun.'
        exit 1
    }
}
