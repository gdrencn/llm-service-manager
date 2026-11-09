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
    try { $mode = (Invoke-Native 'wsl.exe' @('-d', $Distribution, '--cd', '/', '--exec', 'wslinfo', '--networking-mode')).Trim() }
    catch { throw ("Cannot query WSL networking. Confirm the selected distribution uses WSL2 (wsl --list --verbose) and update WSL (wsl --update), then retry. Details: " + $_.Exception.Message) }
    if ($mode -ne 'nat') { throw "Expected NAT, got '$mode'. No forwarding configured." }
    try { $routeText = Invoke-Native 'wsl.exe' @('-d', $Distribution, '--cd', '/', '--exec', 'ip', '-j', '-4', 'route', 'get', '1.1.1.1') }
    catch { throw ("Cannot read Linux IPv4 routing. In the selected distribution, ensure networking works and install its standard iproute2 (apt) or iproute (dnf) package if ip is missing. Details: " + $_.Exception.Message) }
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
        if ($line -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
            [pscustomobject]@{ Listen = $Matches[1]; Port = [int]$Matches[2]; Target = $Matches[3]; TargetPort = [int]$Matches[4] }
        }
    }
}

function Write-PSCommand([string]$Command, [hashtable]$Parameters) {
    $parts = @($Command)
    foreach ($key in @($Parameters.Keys | Sort-Object)) {
        $parts += '-' + $key
        $parts += "'" + ([string]$Parameters[$key]).Replace("'", "''") + "'"
    }
    Write-Host ('$ ' + ($parts -join ' ')) -ForegroundColor DarkGray
}

function Test-FirewallTarget($Rules, [int]$Port, [string]$Owner) {
    $rows = @($Rules)
    if ($rows.Count -ne 1) { return $false }
    $rule = $rows[0]
    if ($rule.Description -ne $Owner -or [string]$rule.Enabled -ne 'True' -or [string]$rule.Direction -ne 'Inbound' -or [string]$rule.Action -ne 'Allow' -or [string]$rule.Profile -ne 'Private') { return $false }
    Write-Host '$ rule | Get-NetFirewallPortFilter; Get-NetFirewallAddressFilter; Get-NetFirewallApplicationFilter; Get-NetFirewallServiceFilter' -ForegroundColor DarkGray
    $ports = $rule | Get-NetFirewallPortFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $application = $rule | Get-NetFirewallApplicationFilter
    $service = $rule | Get-NetFirewallServiceFilter
    return ([string]$ports.Protocol -in @('TCP', '6') -and [string]$ports.LocalPort -eq [string]$Port -and [string]$ports.RemotePort -eq 'Any' -and [string]$address.RemoteAddress -eq 'LocalSubnet' -and [string]$address.LocalAddress -eq 'Any' -and [string]$application.Program -eq 'Any' -and [string]$service.Service -eq 'Any')
}

function Get-LanInterfaces([int]$ServicePort, [string]$IP) {
    Write-Step '[2/6] Check the LAN and backend' 'Allow only Private networks and LocalSubnet. Keep existing network profiles unchanged.'
    Write-Host '$ Get-NetConnectionProfile; Get-NetIPConfiguration' -ForegroundColor DarkGray
    $profiles = @(Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Private')
    $lan = @(Get-NetIPConfiguration | Where-Object { $_.IPv4Address -and ($profiles.InterfaceIndex -contains $_.InterfaceIndex) })
    if ($lan.Count -eq 0) { throw 'No Private LAN with an IPv4 address. In Windows Settings > Network & internet, open your trusted Ethernet/Wi-Fi connection and choose Private, then rerun.' }
    Write-Host '$ Get-Service BFE,MpsSvc; Get-NetFirewallProfile -Name Private' -ForegroundColor DarkGray
    foreach ($service in @('BFE', 'MpsSvc')) {
        if ((Get-Service $service).Status -ne 'Running') { throw "Windows firewall dependency $service is not running. Restore the built-in Windows firewall services, then rerun; this script does not change system-wide service policy." }
    }
    if (-not (Get-NetFirewallProfile -Name Private).Enabled) { throw 'The Private network firewall is disabled. Enable it in Windows Security > Firewall & network protection > Private network, then rerun. Adding a port rule alone would not enforce the requested scope.' }
    Write-Host ('$ Test-NetConnection -ComputerName ' + $IP + ' -Port ' + $ServicePort + ' -InformationLevel Quiet') -ForegroundColor DarkGray
    if (-not (Test-NetConnection -ComputerName $IP -Port $ServicePort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        throw 'Windows cannot reach the Linux service. Apply the Linux listening/firewall configuration and start the model first.'
    }

    return $lan
}

function Get-PortState([string]$Distribution, [int]$ServicePort, [string]$IP) {
    Write-Step '[3/6] Check Windows port ownership' 'Do not replace another program or another distribution forwarding rule.'
    $proxy = @(Get-ProxyRows (Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', 'v4tov4')))
    $existing = @($proxy | Where-Object { $_.Port -eq $ServicePort })
    $name = "llmsm-LAN-TCP-$ServicePort"
    # The description is a native rule property, retained without llmsm installed.
    $owner = "WSL NAT: $Distribution"
    Write-Host ('$ Get-NetFirewallRule; Get-NetTCPConnection -State Listen # TCP ' + $ServicePort) -ForegroundColor DarkGray
    $owned = @(Get-NetFirewallRule -PolicyStore PersistentStore | Where-Object Name -eq $name)
    if ($owned.Count -gt 1) { throw "Multiple firewall rules use $name; resolve the duplicate rules before continuing." }
    if ($owned.Count -gt 0 -and @($owned | Where-Object Description -ne $owner).Count -gt 0) {
        throw "Firewall rule $name already exists with another owner; inspect it before continuing."
    }
    if (@($existing | Where-Object { $_.Listen -ne '0.0.0.0' -or $_.TargetPort -ne $ServicePort -or ($_.Target -ne $IP -and $owned.Count -ne 1) }).Count -gt 0) {
        throw 'A different portproxy already uses this port. Choose another port or resolve the conflict manually.'
    }
    # Other proxy families may occupy the same Windows port. Never replace them.
    foreach ($family in @('v4tov6', 'v6tov4', 'v6tov6')) {
        $other = @(Get-ProxyRows (Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', $family)))
        if (@($other | Where-Object Port -eq $ServicePort).Count -gt 0) {
            throw "Another portproxy family ($family) uses this port. Resolve the conflict before continuing."
        }
    }
    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq $ServicePort)
    if ($existing.Count -eq 0 -and $listeners.Count -gt 0) { throw 'Windows TCP port is occupied. Change the service port and rerun.' }
    $excluded = Invoke-Native 'netsh.exe' @('interface', 'ipv4', 'show', 'excludedportrange', 'protocol=tcp')
    foreach ($line in $excluded -split "`r?`n") {
        if ($line -match '^\s*(\d+)\s+(\d+)\s*\*?\s*$' -and $ServicePort -ge [int]$Matches[1] -and $ServicePort -le [int]$Matches[2] -and $existing.Count -eq 0) {
            throw 'Windows reserves this port. Choose another service port.'
        }
    }

    return @{ Existing = $existing; Owned = $owned; Name = $name; Owner = $owner }
}

function Set-PortForwarding([int]$ServicePort, [string]$IP, [hashtable]$State, [bool]$ReadOnly) {
    $existing = $State.Existing
    Write-Step '[4/6] Configure native TCP forwarding' 'IP Helper runs the Windows portproxy. The rule persists; rerun this script if WSL gets a new IP.'
    Write-Host '$ Get-Service iphlpsvc' -ForegroundColor DarkGray
    $helper = Get-Service iphlpsvc
    if ($helper.StartType -eq 'Disabled') { throw 'IP Helper is disabled. Windows portproxy requires it; restore IP Helper in Windows Services, then rerun.' }
    $proxyReady = ($existing.Count -eq 1 -and $existing[0].Target -eq $IP)
    $verb = if ($existing.Count -gt 0) { 'set' } else { 'add' }
    $proxyArgs = @('interface', 'portproxy', $verb, 'v4tov4', 'listenaddress=0.0.0.0', "listenport=$ServicePort", "connectaddress=$IP", "connectport=$ServicePort")
    if ($ReadOnly) {
        if ($helper.Status -ne 'Running') { Write-Host 'Would run: Start-Service iphlpsvc' }
        if (-not $proxyReady) { Write-Host ('Would run: netsh.exe ' + ($proxyArgs -join ' ')) }
        else { Write-Host 'TCP forwarding already matches; no write needed.' }
    } else {
        if ($helper.Status -ne 'Running') {
            Write-Host '$ Start-Service iphlpsvc' -ForegroundColor DarkGray
            Start-Service iphlpsvc
        }
        if (-not $proxyReady) { Invoke-Native 'netsh.exe' $proxyArgs | Write-Host }
        else { Write-Host 'TCP forwarding already matches; no write needed.' }
    }

}

function Set-LocalFirewall([int]$ServicePort, [hashtable]$State, [bool]$ReadOnly) {
    $name = $State.Name; $owner = $State.Owner; $owned = $State.Owned
    Write-Step '[5/6] Configure the native firewall rule' 'Only this named inbound TCP rule is changed. Other rules, default policies and network profiles stay intact.'
    $rule = @{ Name = $name; Description = $owner; PolicyStore = 'PersistentStore'; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = $ServicePort; RemotePort = 'Any'; LocalAddress = 'Any'; Profile = 'Private'; RemoteAddress = 'LocalSubnet'; Program = 'Any'; Service = 'Any'; Enabled = 'True' }
    $operation = if ($owned.Count -gt 0) { 'Set-NetFirewallRule' } else { 'New-NetFirewallRule' }
    if ($owned.Count -eq 0) { $rule.DisplayName = "LLM LAN TCP $ServicePort" }
    if (Test-FirewallTarget $owned $ServicePort $owner) {
        Write-Host 'Firewall rule already matches; no write needed.'
    } else {
        Write-PSCommand $operation $rule
        if (-not $ReadOnly) { & $operation @rule | Out-Null }
    }

}

function Confirm-LanSetup([int]$ServicePort, [string]$IP, [hashtable]$State, $Lan, [bool]$ReadOnly) {
    $name = $State.Name; $owner = $State.Owner
    Write-Step '[6/6] Verify and show client addresses' 'A local check cannot prove access from another computer; test the URL there as well.'
    if ($ReadOnly) { Write-Host 'Check-only: no Windows configuration was changed.'; return }
    $verified = @(Get-ProxyRows (Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', 'v4tov4')) | Where-Object { $_.Listen -eq '0.0.0.0' -and $_.Port -eq $ServicePort -and $_.Target -eq $IP -and $_.TargetPort -eq $ServicePort })
    if ($verified.Count -ne 1) { throw 'Forwarding verification failed; inspect the displayed commands.' }
    Write-Host ('$ Get-NetFirewallRule -PolicyStore ActiveStore -Name ' + $name + ' # verify rule, port and address filters') -ForegroundColor DarkGray
    $active = Get-NetFirewallRule -PolicyStore ActiveStore -Name $name
    if (-not (Test-FirewallTarget $active $ServicePort $owner)) {
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

function Invoke-LanSetup([string]$Distribution, [int]$ServicePort, [bool]$ReadOnly, [string]$IP) {
    # Read, validate, configure native targets, then verify. No WSL access after elevation.
    Assert-Target $Distribution $ServicePort $IP
    $lan = @(Get-LanInterfaces $ServicePort $IP)
    $state = Get-PortState $Distribution $ServicePort $IP
    Set-PortForwarding $ServicePort $IP $state $ReadOnly
    Set-LocalFirewall $ServicePort $state $ReadOnly
    Confirm-LanSetup $ServicePort $IP $state $lan $ReadOnly
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
    $required = @('wsl.exe', 'netsh.exe', 'Get-NetConnectionProfile', 'Get-NetIPConfiguration',
                  'Test-NetConnection', 'Get-NetTCPConnection', 'Get-NetFirewallRule',
                  'Get-NetFirewallPortFilter', 'Get-NetFirewallAddressFilter', 'Get-NetFirewallProfile',
                  'Get-NetFirewallApplicationFilter', 'Get-NetFirewallServiceFilter',
                  'New-NetFirewallRule', 'Set-NetFirewallRule', 'Get-Service', 'Start-Service')
    foreach ($name in $required) {
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
            if ($name -eq 'wsl.exe') { throw 'WSL is not installed. Install WSL2 and prepare the Linux model service, then rerun this script.' }
            throw "Required Windows built-in command is unavailable: $name. Use Windows PowerShell 5.1 and restore the built-in NetTCPIP/NetSecurity components if missing; no third-party package is required."
        }
    }
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
        Write-Host 'Stopped without proceeding to later steps. Completed native changes remain in place; follow the guidance above, then rerun.'
        exit 1
    }
}
