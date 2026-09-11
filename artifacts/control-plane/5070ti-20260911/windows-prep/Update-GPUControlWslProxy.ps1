#requires -Version 5.1
<#
Run as the Windows user who owns the WSL distribution, elevated.
Default: read-only status. -Apply repairs only this node's recorded mappings.
-Watch keeps WSL alive and checks the mappings every 60 seconds.
Configuration contains public addresses and names only; no credentials.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [switch]$Apply,
    [switch]$Watch
)

# Local adaptation: accept overlapping allow rules only while an exact,
# all-profile block rule independently rejects every source except the controller.
function Test-GpuControlSourceBlock([string]$Control, [int[]]$ManagedPorts) {
    if ($Control -ne '10.3.34.11') { return $false }
    $b = Get-NetFirewallRule -PolicyStore ActiveStore -Name 'GPUControl-worker-5070ti-01-Management-BlockOtherSources' -ErrorAction SilentlyContinue
    if (-not $b -or [string]$b.Enabled -ne 'True' -or [string]$b.Direction -ne 'Inbound' -or [string]$b.Action -ne 'Block' -or [string]$b.Profile -ne 'Any') { return $false }
    $a = $b | Get-NetFirewallAddressFilter
    $p = $b | Get-NetFirewallPortFilter
    $expected = '["128.0.0.0/128.0.0.0","64.0.0.0/192.0.0.0","32.0.0.0/224.0.0.0","16.0.0.0/240.0.0.0","0.0.0.0/248.0.0.0","12.0.0.0/252.0.0.0","8.0.0.0/254.0.0.0","11.0.0.0/255.0.0.0","10.128.0.0/255.128.0.0","10.64.0.0/255.192.0.0","10.32.0.0/255.224.0.0","10.16.0.0/255.240.0.0","10.8.0.0/255.248.0.0","10.4.0.0/255.252.0.0","10.0.0.0/255.254.0.0","10.2.0.0/255.255.0.0","10.3.128.0/255.255.128.0","10.3.64.0/255.255.192.0","10.3.0.0/255.255.224.0","10.3.48.0/255.255.240.0","10.3.40.0/255.255.248.0","10.3.36.0/255.255.252.0","10.3.32.0/255.255.254.0","10.3.35.0/255.255.255.0","10.3.34.128/255.255.255.128","10.3.34.64/255.255.255.192","10.3.34.32/255.255.255.224","10.3.34.16/255.255.255.240","10.3.34.0/255.255.255.248","10.3.34.12/255.255.255.252","10.3.34.8/255.255.255.254","10.3.34.10","::/1","8000::/1"]' | ConvertFrom-Json
    if ((@($a.LocalAddress) -join ',') -ne 'Any' -or (@($a.RemoteAddress | Sort-Object) -join ',') -ne (@($expected | Sort-Object) -join ',')) { return $false }
    if ([string]$p.Protocol -notin @('TCP','6')) { return $false }
    foreach ($port in $ManagedPorts) { if ([string]$port -notin @($p.LocalPort)) { return $false } }
    if ([string]($b | Get-NetFirewallApplicationFilter).Program -ne 'Any' -or [string]($b | Get-NetFirewallServiceFilter).Service -ne 'Any') { return $false }
    return $true
}
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Watch -and -not $Apply) { throw '-Watch requires -Apply.' }
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$WslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
$StatePath = Join-Path (Split-Path -Parent $ConfigPath) 'proxy-ownership.json'
$LogPath = Join-Path (Split-Path -Parent $ConfigPath) 'proxy.log'

function Assert-IPv4([string]$Address) {
    $Parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$Parsed) -or
        $Parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $Address -in @('0.0.0.0', '255.255.255.255') -or $Address.StartsWith('127.')) {
        throw "Expected a unicast IPv4 address: $Address"
    }
}

Assert-IPv4 $Config.WindowsIP
Assert-IPv4 $Config.ControlAddress
if ($Config.NodeName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$' -or
    $Config.Distro -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$') {
    throw 'Invalid node name or distribution name.'
}
$Ports = @($Config.Mappings | ForEach-Object {
    if ([int]$_.ListenPort -lt 1 -or [int]$_.ListenPort -gt 65535 -or
        [int]$_.ConnectPort -lt 1 -or [int]$_.ConnectPort -gt 65535) {
        throw 'Invalid port in configuration.'
    }
    [string]$_.ListenPort
})
if ($Ports.Count -eq 0 -or @($Ports | Select-Object -Unique).Count -ne $Ports.Count) {
    throw 'Configuration has missing or duplicate listening ports.'
}
$RuleName = "GPUControl-$($Config.NodeName)-WSL-From-Control"

function Get-ProxyMappings {
    $Lines = & netsh.exe interface portproxy show v4tov4
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Windows portproxy.' }
    foreach ($Line in $Lines) {
        $Fields = @($Line.Trim() -split '\s+')
        if ($Fields.Count -eq 4 -and $Fields[1] -match '^\d+$' -and $Fields[3] -match '^\d+$') {
            [pscustomobject]@{
                ListenAddress = $Fields[0]; ListenPort = [int]$Fields[1]
                ConnectAddress = $Fields[2]; ConnectPort = [int]$Fields[3]
            }
        }
    }
}

function Write-Change([string]$Message) {
    # Rotate bounded local diagnostics; never log keys or WSL command output.
    if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -gt 1048576) {
        Move-Item -LiteralPath $LogPath -Destination "$LogPath.previous" -Force
    }
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value (
        '{0} {1}' -f (Get-Date).ToUniversalTime().ToString('o'), $Message
    )
}

function Assert-NoBroadPortRule {
    # Do not silently disable someone else's firewall rules. An explicit rule for
    # a managed port from another source must be reconciled before publishing it.
    foreach ($Profile in @(Get-NetFirewallProfile -PolicyStore ActiveStore)) {
        if (-not $Profile.Enabled -or [string]$Profile.DefaultInboundAction -eq 'Allow') {
            throw "Firewall profile '$($Profile.Name)' must be enabled with inbound blocking before publishing ports."
        }
    }
    $ControlOnlyBlockVerified = Test-GpuControlSourceBlock -Control $Config.ControlAddress -ManagedPorts @($Ports | ForEach-Object { [int]$_ })
    foreach ($Rule in @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow)) {
        if ($ControlOnlyBlockVerified) { continue }
        if ($Rule.Name -eq $RuleName) { continue }
        $Filter = $Rule | Get-NetFirewallPortFilter
        if ([string]$Filter.Protocol -notin @('TCP', '6', 'Any', '256')) { continue }
        $LocalPorts = @($Filter.LocalPort | ForEach-Object { [string]$_ })
        $Overlap = $LocalPorts -contains 'Any'
        foreach ($PortRange in $LocalPorts) {
            if ($Ports -contains $PortRange) { $Overlap = $true }
            if ($PortRange -match '^(\d+)-(\d+)$') {
                $Low = [int]$Matches[1]; $High = [int]$Matches[2]
                if (@($Ports | Where-Object { [int]$_ -ge $Low -and [int]$_ -le $High }).Count -gt 0) { $Overlap = $true }
            }
        }
        if (-not $Overlap) { continue }
        $Program = [string]($Rule | Get-NetFirewallApplicationFilter).Program
        $Service = [string]($Rule | Get-NetFirewallServiceFilter).Service
        if ($Program -notin @('Any', 'System') -and $Program -notmatch '(?i)[\\/](svchost|sshd)\.exe$') { continue }
        if ($Service -notin @('Any', 'iphlpsvc', 'sshd')) { continue }
        $Remote = @(($Rule | Get-NetFirewallAddressFilter).RemoteAddress)
        if ($Remote.Count -ne 1 -or $Remote[0] -ne $Config.ControlAddress) {
            throw "Firewall rule '$($Rule.Name)' already exposes a managed port to other sources. Review it locally."
        }
    }
}

function Sync-Proxy {
    $CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($CurrentIdentity.User.Value -ne $Config.OwnerSid) {
        throw 'Run under the Windows user who owns the configured WSL distribution.'
    }
    $Principal = New-Object Security.Principal.WindowsPrincipal($CurrentIdentity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Applying portproxy requires an elevated process.'
    }
    if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Config.WindowsIP -ErrorAction SilentlyContinue)) {
        throw 'The configured Windows IP is not assigned to this host; no mapping was changed.'
    }
    Assert-NoBroadPortRule
    $AddressJson = & $WslExe -d $Config.Distro -u root -- ip -j -4 addr show dev eth0
    if ($LASTEXITCODE -ne 0) { throw 'Cannot discover the configured WSL distribution address.' }
    $AddressInfo = ($AddressJson -join "`n") | ConvertFrom-Json
    $WslAddresses = @($AddressInfo.addr_info | Where-Object { $_.family -eq 'inet' -and $_.scope -eq 'global' })
    if ($WslAddresses.Count -ne 1) { throw 'Expected exactly one WSL NAT IPv4 address on eth0.' }
    $WslIP = [string]$WslAddresses[0].local
    Assert-IPv4 $WslIP
    if ($WslIP -eq $Config.WindowsIP) { throw 'This script requires WSL NAT networking; mirrored mode needs separate configuration.' }
    $Existing = @(Get-ProxyMappings)
    $Owned = @()
    if (Test-Path -LiteralPath $StatePath) {
        $Owned = @(Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json)
    }
    foreach ($Mapping in $Config.Mappings) {
        $OwnsMapping = @($Owned | Where-Object {
            $_.ListenAddress -eq $Config.WindowsIP -and $_.ListenPort -eq $Mapping.ListenPort -and
            $_.ConnectPort -eq $Mapping.ConnectPort
        }).Count -eq 1
        $Matching = @($Existing | Where-Object {
            $_.ListenAddress -eq $Config.WindowsIP -and $_.ListenPort -eq $Mapping.ListenPort
        })
        if ($Matching.Count -gt 0 -and -not $OwnsMapping) {
            throw "Port $($Mapping.ListenPort) has a pre-existing unowned mapping; refusing to replace it."
        }
        if ($Matching.Count -eq 0) {
            $Listener = @(Get-NetTCPConnection -State Listen -LocalPort $Mapping.ListenPort -ErrorAction SilentlyContinue |
                Where-Object { $_.LocalAddress -in @('0.0.0.0', '::', $Config.WindowsIP) })
            if ($Listener.Count -gt 0) { throw "Port $($Mapping.ListenPort) is already used by another Windows listener." }
        }
    }
    $Rule = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    $RuleMatches = $false
    if ($Rule) {
        $AddressFilter = $Rule | Get-NetFirewallAddressFilter
        $PortFilter = $Rule | Get-NetFirewallPortFilter
        $RuleMatches = $Rule.Enabled -eq 'True' -and $Rule.Direction -eq 'Inbound' -and $Rule.Action -eq 'Allow' -and
            [string]$PortFilter.Protocol -in @('TCP', '6') -and
            @($AddressFilter.LocalAddress).Count -eq 1 -and @($AddressFilter.LocalAddress)[0] -eq $Config.WindowsIP -and
            @($AddressFilter.RemoteAddress).Count -eq 1 -and @($AddressFilter.RemoteAddress)[0] -eq $Config.ControlAddress -and
            (@($PortFilter.LocalPort | Sort-Object) -join ',') -eq (@($Ports | Sort-Object) -join ',')
    }
    if (-not $RuleMatches) {
        if ($Rule) { $Rule | Remove-NetFirewallRule }
        New-NetFirewallRule -Name $RuleName -DisplayName $RuleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalAddress $Config.WindowsIP -LocalPort $Ports `
            -RemoteAddress $Config.ControlAddress -Profile Any | Out-Null
        Write-Change "firewall_updated local=$($Config.WindowsIP) remote=$($Config.ControlAddress) ports=$($Ports -join ',')"
    }
    Set-Service iphlpsvc -StartupType Automatic
    Start-Service iphlpsvc
    # Record ownership before mutation so an interrupted first application can be retried.
    @($Config.Mappings | ForEach-Object {
        [pscustomobject]@{ ListenAddress = $Config.WindowsIP; ListenPort = $_.ListenPort; ConnectPort = $_.ConnectPort }
    }) | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8
    foreach ($Mapping in $Config.Mappings) {
        $Matching = @($Existing | Where-Object {
            $_.ListenAddress -eq $Config.WindowsIP -and $_.ListenPort -eq $Mapping.ListenPort
        })
        if ($Matching.Count -eq 1 -and $Matching[0].ConnectAddress -eq $WslIP -and
            $Matching[0].ConnectPort -eq $Mapping.ConnectPort) { continue }
        $Operation = 'add'
        if ($Matching.Count -gt 0) { $Operation = 'set' }
        & netsh.exe interface portproxy $Operation v4tov4 "listenaddress=$($Config.WindowsIP)" `
            "listenport=$($Mapping.ListenPort)" "connectaddress=$WslIP" `
            "connectport=$($Mapping.ConnectPort)" protocol=tcp | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not update portproxy for port $($Mapping.ListenPort)." }
        Write-Change "proxy_updated local=$($Config.WindowsIP):$($Mapping.ListenPort) target=${WslIP}:$($Mapping.ConnectPort)"
    }
    Write-Output "WSL_IPV4=$WslIP"
}

if (-not $Apply) {
    [pscustomobject]@{
        Mode = 'Inspect'; WindowsIP = $Config.WindowsIP; Distro = $Config.Distro
        CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        ConfiguredOwnerSid = $Config.OwnerSid; Ports = $Ports
        Mappings = @(Get-ProxyMappings | Where-Object { $_.ListenAddress -eq $Config.WindowsIP })
        Firewall = @(Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue |
            Select-Object Name, Enabled, Direction, Action)
    } | ConvertTo-Json -Depth 6
    return
}
if (-not $Watch) { Sync-Proxy; return }

$Keepalive = $null
try {
    while ($true) {
        try {
            # The blocking WSL process is the keepalive; systemd services alone
            # are not a guarantee that a Windows WSL instance remains alive.
            if ($null -eq $Keepalive -or $Keepalive.HasExited) {
                $Keepalive = Start-Process -FilePath $WslExe -ArgumentList @(
                    '-d', $Config.Distro, '-u', 'root', '--', '/usr/bin/sleep', 'infinity'
                ) -WindowStyle Hidden -PassThru
            }
            Sync-Proxy
        } catch {
            Write-Change "repair_failed $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 60
    }
} finally {
    if ($null -ne $Keepalive -and -not $Keepalive.HasExited) { $Keepalive.Kill() }
}
