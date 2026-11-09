# Standalone regression harness; no Pester and no real network mutations.
param([string]$Source, [string]$SourcePath)
if ($SourcePath) { $Source = [IO.File]::ReadAllText($SourcePath) }
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseInput($Source, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
. ([scriptblock]::Create($Source))
$script:changes = 0
$script:mode = 'nat'
$script:proxyText = ''
$script:otherProxyText = ''
$script:fw = @()
$script:busy = $false
$script:reachable = $true
function Invoke-Native([string]$Program, [string[]]$Arguments) {
    $cmd = $Arguments -join ' '
    if ($Program -eq 'wsl.exe') {
        if ($cmd -eq '--list --quiet') { return "Ubuntu`nllm" }
        if ($cmd -like '*--networking-mode') { return $script:mode }
        return '[{"prefsrc":"172.28.1.2"}]'
    }
    if ($cmd -eq 'interface portproxy show v4tov4') { return $script:proxyText }
    if ($cmd -match 'interface portproxy show (v4tov6|v6tov4|v6tov6)') { return $script:otherProxyText }
    if ($cmd -like '*excludedportrange*') { return '' }
    if ($cmd -match 'portproxy (add|set)') { $script:changes++; $script:proxyText = '0.0.0.0 55555 172.28.1.2 55555'; return 'OK' }
    throw "Unexpected native command $cmd"
}
function Get-NetConnectionProfile { [pscustomobject]@{NetworkCategory=$(if ($script:privateProfile) {'Private'} else {'Public'});InterfaceIndex=4} }
function Get-NetIPConfiguration { [pscustomobject]@{IPv4DefaultGateway=$null;InterfaceIndex=4;IPv4Address=@([pscustomobject]@{IPAddress='192.168.1.20'})} }
function Test-NetConnection { return $script:reachable }
function Get-NetFirewallRule { return $script:fw }
function Get-NetTCPConnection { if ($script:busy) { [pscustomobject]@{LocalPort=55555} } }
$script:firewallEnabled = $true
$script:serviceState = 'Running'
$script:privateProfile = $true
function Get-NetFirewallProfile { [pscustomobject]@{Enabled=$script:firewallEnabled} }
function Get-Service { [pscustomobject]@{Status=$script:serviceState;StartType='Automatic'} }
function Start-Service { $script:changes++ }
function New-NetFirewallRule {
    $script:changes++
    $script:fw = @([pscustomobject]@{Name='llmsm-LAN-TCP-55555';Description='WSL NAT: test';Enabled='True';Direction='Inbound';Action='Allow';Profile='Private'})
}
function Set-NetFirewallRule { $script:changes++; $script:remoteAddress='LocalSubnet' }
function Get-NetFirewallPortFilter { [pscustomobject]@{Protocol='TCP';LocalPort='55555';RemotePort='Any'} }
$script:remoteAddress = 'LocalSubnet'
function Get-NetFirewallAddressFilter { [pscustomobject]@{RemoteAddress=$script:remoteAddress;LocalAddress='Any'} }
function Get-NetFirewallApplicationFilter { [pscustomobject]@{Program='Any'} }
function Get-NetFirewallServiceFilter { [pscustomobject]@{Service='Any'} }
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Failure([string]$Pattern) {
    $before = $script:changes
    try { Invoke-LanSetup 'test' 55555 $false '172.28.1.2'; throw 'Expected failure' }
    catch { if ($_.Exception.Message -notlike $Pattern) { throw } }
    Assert ($script:changes -eq $before) 'Failure path mutated Windows state'
}
Invoke-LanSetup 'test' 55555 $true '172.28.1.2'
Assert ($script:changes -eq 0) 'CheckOnly mutated Windows state'
Invoke-LanSetup 'test' 55555 $false '172.28.1.2'
Assert ($script:changes -eq 2) 'Expected exactly one native proxy and firewall write'
Invoke-LanSetup 'test' 55555 $false '172.28.1.2'
Assert ($script:changes -eq 2) 'Already-effective settings should not be rewritten'
$script:otherProxyText = ':: 55555 172.28.1.2 55555'; Expect-Failure '*Another portproxy family*'; $script:otherProxyText = ''
$script:remoteAddress = 'Any'
$beforeRepair = $script:changes
Invoke-LanSetup 'test' 55555 $false '172.28.1.2'
Assert ($script:changes -eq $beforeRepair + 1) 'Incorrect firewall scope was not repaired exactly once'
$script:fw += $script:fw[0]; Expect-Failure '*Multiple firewall rules*'; $script:fw = @($script:fw[0])
$script:firewallEnabled = $false; Expect-Failure '*Private network firewall is disabled*'; $script:firewallEnabled = $true
$script:serviceState = 'Stopped'; Expect-Failure '*not running*'; $script:serviceState = 'Running'
$script:privateProfile = $false; Expect-Failure '*choose Private*'; $script:privateProfile = $true
$script:mode = 'mirrored'
try { Resolve-WslTarget 'test' 55555; throw 'Expected NAT failure' } catch { if ($_.Exception.Message -notlike '*Expected NAT*') { throw } }
$script:mode = 'nat'
$script:reachable = $false; Expect-Failure '*cannot reach*'; $script:reachable = $true
$script:proxyText = '0.0.0.0 55555 172.28.1.99 55555'; $script:fw = @()
Expect-Failure '*different portproxy*'
$script:proxyText = '0.0.0.0 55555 another-host.local 55555'; Expect-Failure '*different portproxy*'
$script:proxyText = ''; $script:busy = $true; Expect-Failure '*occupied*'; $script:busy = $false
$script:fw = @([pscustomobject]@{Name='llmsm-LAN-TCP-55555';Description='someone else'})
Expect-Failure '*another owner*'
# Interactive validation never passes unfilled placeholders into WSL.
$script:answers = [Collections.Generic.Queue[string]]::new()
foreach ($answer in @('bad', '3', '2', '70000', '55556')) { $script:answers.Enqueue($answer) }
function Read-Host { return $script:answers.Dequeue() }
$selected = Select-Target
Assert ($selected.distro -eq 'llm' -and $selected.port -eq 55556) 'Interactive selection failed'
$script:answers.Enqueue('q')
Assert ($null -eq (Select-Target)) 'Cancel should not request elevation'
$script:answers.Enqueue(''); $script:answers.Enqueue('')
$selected = Select-Target
Assert ($selected.distro -eq 'Ubuntu' -and $selected.port -eq 55555) 'Defaults failed'
try { Assert-Target '<placeholder>' 55555 '172.28.1.2'; throw 'Expected invalid target' } catch { if ($_.Exception.Message -notlike '*Invalid distribution*') { throw } }
# Missing WSL-side tools give explicit next steps without installing Windows extras.
$savedNative = ${function:Invoke-Native}
function Invoke-Native { throw 'command missing' }
try { Resolve-WslTarget 'test' 55555; throw 'Expected WSL guidance' } catch { if ($_.Exception.Message -notlike '*wsl --update*') { throw } }
function Invoke-Native([string]$Program, [string[]]$Arguments) { if ($Arguments -contains 'wslinfo') { return 'nat' }; throw 'ip missing' }
try { Resolve-WslTarget 'test' 55555; throw 'Expected ip guidance' } catch { if ($_.Exception.Message -notlike '*iproute2*iproute*') { throw } }
${function:Invoke-Native} = $savedNative
# The Windows-only worker never tries to enter WSL after changing identity.
function Invoke-Native([string]$Program, [string[]]$Arguments) { if ($Program -eq 'wsl.exe') { throw 'Worker must not access WSL' }; if (($Arguments -join ' ') -like '*portproxy show*') { return '' }; return '' }
$script:fw = @(); Invoke-LanSetup 'test' 55555 $true '172.28.1.2'
# Prerequisite diagnostics distinguish missing binaries, settings and policy.
$realSudoCheck = ${function:Get-InlineSudo}
$script:binaryPresent = $false
$script:modeValue = $null
$script:policyValue = $null
function Test-Path { return $script:binaryPresent }
function Get-ItemProperty([string]$Path) {
    if ($Path -like '*Policies*') { return @{Enabled=$script:policyValue} }
    return @{Enabled=$script:modeValue}
}
try { Get-InlineSudo; throw 'Expected missing sudo' } catch { if ($_.Exception.Message -notlike '*missing*Windows 11 24H2*') { throw } }
$script:binaryPresent = $true
foreach ($mode in @($null, 0, 1, 2)) {
    $script:modeValue = $mode
    try { Get-InlineSudo; throw 'Expected setup guidance' } catch {
        if ($_.Exception.Message -notlike '*sudo config --enable normal*ordinary PowerShell*') { throw }
    }
}
$script:modeValue = 3
Assert ((Get-InlineSudo) -like '*sudo.exe') 'Enabled Inline mode was rejected'
$script:policyValue = 1
try { Get-InlineSudo; throw 'Expected policy restriction' } catch { if ($_.Exception.Message -notlike '*policy restricts*administrator*') { throw } }
$script:policyValue = $null
Assert ($Source -notmatch 'Start-Process|RunAs|EventLog|WorkerRequest|Receive-Events') 'Fallback machinery must not be present'
# The only elevation path is sudo --inline; target is passed as structured data.
$script:sudoCalls = 0
$script:sudoExit = 0
function Get-InlineSudo { return 'Invoke-FakeSudo' }
function Select-Target { return @{distro='test server';port=55556} }
function Resolve-WslTarget { return '172.28.1.2' }
function Invoke-FakeSudo {
    $script:sudoCalls++
    Assert ($args[0] -eq '--inline') 'Must use inline sudo'
    $target = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($args[-1])) | ConvertFrom-Json
    Assert ($target.distro -eq 'test server' -and $target.port -eq 55556 -and $target.ip -eq '172.28.1.2') 'Target data was corrupted'
    $global:LASTEXITCODE = $script:sudoExit
}
Invoke-Setup $true 'test.ps1'
Assert ($script:sudoCalls -eq 1) 'Sudo was not called exactly once'
$script:sudoExit = 1
try { Invoke-Setup $false 'test.ps1'; throw 'Expected elevation failure' }
catch { if ($_.Exception.Message -notlike '*Configuration failed*') { throw } }
Assert ($script:sudoCalls -eq 2) 'Failed elevation was retried'
Write-Host 'WINDOWS_MOCK_REGRESSIONS_PASSED'

# Missing Windows built-in module must be diagnosed before any network mutation.
function Get-Command([string]$Name) { if ($Name -eq 'Get-NetFirewallProfile') { return $null }; return @{Name=$Name} }
try { Require-Environment; throw 'Expected missing component' } catch { if ($_.Exception.Message -notlike '*Get-NetFirewallProfile*NetTCPIP/NetSecurity*') { throw } }
Write-Host 'FRESH_WINDOWS_REQUIREMENTS_PASSED'
