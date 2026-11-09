# Standalone regression harness; no Pester and no real network mutations.
param([string]$Source)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseInput($Source, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
. ([scriptblock]::Create($Source)) -Distro 'test'
$script:changes = 0
$script:mode = 'nat'
$script:proxyText = ''
$script:fw = @()
$script:busy = $false
$script:reachable = $true
function Invoke-Native([string]$Program, [string[]]$Arguments) {
    $cmd = $Arguments -join ' '
    if ($Program -eq 'wsl.exe') {
        if ($cmd -like '*--networking-mode') { return $script:mode }
        return '[{"prefsrc":"172.28.1.2"}]'
    }
    if ($cmd -eq 'interface portproxy show v4tov4') { return $script:proxyText }
    if ($cmd -like '*excludedportrange*') { return '' }
    if ($cmd -match 'portproxy (add|set)') { $script:changes++; $script:proxyText = '0.0.0.0 55555 172.28.1.2 55555'; return 'OK' }
    throw "Unexpected native command $cmd"
}
function Get-NetConnectionProfile { [pscustomobject]@{NetworkCategory='Private';InterfaceIndex=4} }
function Get-NetIPConfiguration { [pscustomobject]@{IPv4DefaultGateway='192.168.1.1';InterfaceIndex=4;IPv4Address=@([pscustomobject]@{IPAddress='192.168.1.20'})} }
function Test-NetConnection { return $script:reachable }
function Get-NetFirewallRule { return $script:fw }
function Get-NetTCPConnection { if ($script:busy) { [pscustomobject]@{LocalPort=55555} } }
function Get-Service { [pscustomobject]@{Status='Running';StartType='Automatic'} }
function Start-Service { $script:changes++ }
function New-NetFirewallRule {
    $script:changes++
    $script:fw = @([pscustomobject]@{Name='llmsm-LAN-TCP-55555';Description='WSL NAT: test';Enabled='True';Direction='Inbound';Action='Allow';Profile='Private'})
}
function Set-NetFirewallRule { $script:changes++ }
function Get-NetFirewallPortFilter { [pscustomobject]@{Protocol='TCP';LocalPort='55555'} }
function Get-NetFirewallAddressFilter { [pscustomobject]@{RemoteAddress='LocalSubnet'} }
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Failure([string]$Pattern) {
    $before = $script:changes
    try { Invoke-LanSetup 'test' 55555 $false; throw 'Expected failure' }
    catch { if ($_.Exception.Message -notlike $Pattern) { throw } }
    Assert ($script:changes -eq $before) 'Failure path mutated Windows state'
}
Invoke-LanSetup 'test' 55555 $true
Assert ($script:changes -eq 0) 'CheckOnly mutated Windows state'
Invoke-LanSetup 'test' 55555 $false
Assert ($script:changes -eq 2) 'Expected exactly one native proxy and firewall write'
Invoke-LanSetup 'test' 55555 $false
Assert ($script:changes -eq 4) 'Rerun failed to update native configuration'
$script:mode = 'mirrored'; Expect-Failure '*Expected NAT*'; $script:mode = 'nat'
$script:reachable = $false; Expect-Failure '*cannot reach*'; $script:reachable = $true
$script:proxyText = '0.0.0.0 55555 172.28.1.99 55555'; $script:fw = @()
Expect-Failure '*different portproxy*'
$script:proxyText = ''; $script:busy = $true; Expect-Failure '*occupied*'; $script:busy = $false
$script:fw = @([pscustomobject]@{Name='llmsm-LAN-TCP-55555';Description='someone else'})
Expect-Failure '*another owner*'
Write-Host 'WINDOWS_MOCK_REGRESSIONS_PASSED'
