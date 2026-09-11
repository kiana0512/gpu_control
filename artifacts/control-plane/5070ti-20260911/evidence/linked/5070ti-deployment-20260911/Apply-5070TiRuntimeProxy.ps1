#requires -Version 5.1
<#
Add four runtime proxies on the verified 5070 Ti Windows host.
Reuses the exact locally adapted proxy script. Keeps the management task and
its keepalive process running; uses a separate config, state, firewall allow,
and task for runtime ports. Default is review only. No Windows/WSL restart.
Use Windows PowerShell with -ExecutionPolicy Bypass for this invocation only,
matching the existing scheduled task; no persistent execution policy is changed.
#>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
$Root = 'C:\ProgramData\GPUControl\worker-5070ti-01'
$ProxyScript = Join-Path $Root 'Update-GPUControlWslProxy.ps1'
$ExpectedScriptHash = '01F695A3DAFA393A8C8BC2EB3A262919B2CFC9D7BA4E217CE1446F569A7BB1B0'
$OriginalConfigPath = Join-Path $Root 'proxy-config.json'
$OriginalTaskName = 'GPUControl-worker-5070ti-01-WSL-Maintainer'
$RuntimeRoot = Join-Path $Root 'runtime-proxy'
$RuntimeConfigPath = Join-Path $RuntimeRoot 'proxy-config.json'
$RuntimeTaskName = 'GPUControl-worker-5070ti-01-WSL-RuntimeProxy'
$RuntimeAllowName = 'GPUControl-worker-5070ti-01-runtime-WSL-From-Control'
$BlockName = 'GPUControl-worker-5070ti-01-Management-BlockOtherSources'
$WindowsIP = '10.3.34.18'
$ControlAddress = '10.3.34.11'
$RuntimePorts = @(8188, 9100, 9201, 9301)
$OwnerSid = 'S-1-5-21-729800975-1068271290-1542459589-1000'
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($Identity.User.Value -ne $OwnerSid) { throw 'Wrong Windows WSL owner.' }
if ((Get-FileHash -LiteralPath $ProxyScript -Algorithm SHA256).Hash -ne $ExpectedScriptHash) {
    throw 'The locally adapted script has changed; re-review it instead of overwriting it.'
}
$OriginalConfig = Get-Content -LiteralPath $OriginalConfigPath -Raw | ConvertFrom-Json
if ($OriginalConfig.WindowsIP -ne $WindowsIP -or $OriginalConfig.ControlAddress -ne $ControlAddress -or
    $OriginalConfig.Distro -ne 'Ubuntu-22.04' -or $OriginalConfig.OwnerSid -ne $OwnerSid -or
    @($OriginalConfig.Mappings).Count -ne 1 -or $OriginalConfig.Mappings[0].ListenPort -ne 2222 -or
    $OriginalConfig.Mappings[0].ConnectPort -ne 22) {
    throw 'Management configuration differs from the reviewed state.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $WindowsIP -ErrorAction SilentlyContinue)) {
    throw 'Windows address is not assigned to this host.'
}
$OriginalTask = Get-ScheduledTask -TaskName $OriginalTaskName
if ([string]$OriginalTask.State -ne 'Running' -or [string]$OriginalTask.Principal.LogonType -ne 'Interactive') {
    throw 'The existing management maintainer must remain running under its interactive owner.'
}
$Block = Get-NetFirewallRule -PolicyStore ActiveStore -Name $BlockName
$BlockAddress = $Block | Get-NetFirewallAddressFilter
$BlockPorts = $Block | Get-NetFirewallPortFilter
$ExpectedBlockedSources = @(
    '128.0.0.0/128.0.0.0', '64.0.0.0/192.0.0.0', '32.0.0.0/224.0.0.0', '16.0.0.0/240.0.0.0',
    '0.0.0.0/248.0.0.0', '12.0.0.0/252.0.0.0', '8.0.0.0/254.0.0.0', '11.0.0.0/255.0.0.0',
    '10.128.0.0/255.128.0.0', '10.64.0.0/255.192.0.0', '10.32.0.0/255.224.0.0', '10.16.0.0/255.240.0.0',
    '10.8.0.0/255.248.0.0', '10.4.0.0/255.252.0.0', '10.0.0.0/255.254.0.0', '10.2.0.0/255.255.0.0',
    '10.3.128.0/255.255.128.0', '10.3.64.0/255.255.192.0', '10.3.0.0/255.255.224.0', '10.3.48.0/255.255.240.0',
    '10.3.40.0/255.255.248.0', '10.3.36.0/255.255.252.0', '10.3.32.0/255.255.254.0', '10.3.35.0/255.255.255.0',
    '10.3.34.128/255.255.255.128', '10.3.34.64/255.255.255.192', '10.3.34.32/255.255.255.224', '10.3.34.16/255.255.255.240',
    '10.3.34.0/255.255.255.248', '10.3.34.12/255.255.255.252', '10.3.34.8/255.255.255.254', '10.3.34.10',
    '::/1', '8000::/1'
)
if ([string]$Block.Enabled -ne 'True' -or [string]$Block.Direction -ne 'Inbound' -or
    [string]$Block.Action -ne 'Block' -or [string]$Block.Profile -ne 'Any' -or
    (@($BlockAddress.LocalAddress) -join ',') -ne 'Any' -or
    (@($BlockAddress.RemoteAddress | Sort-Object) -join ',') -ne (@($ExpectedBlockedSources | Sort-Object) -join ',') -or
    [string]$BlockPorts.Protocol -notin @('TCP', '6') -or
    [string]($Block | Get-NetFirewallApplicationFilter).Program -ne 'Any' -or
    [string]($Block | Get-NetFirewallServiceFilter).Service -ne 'Any') {
    throw 'The pre-existing control-only source block does not match the reviewed security boundary.'
}
foreach ($Port in @(22, 2222)) {
    if ([string]$Port -notin @($BlockPorts.LocalPort)) { throw 'Management port source blocking is missing.' }
}
$NewBlockedPorts = @(@($BlockPorts.LocalPort | ForEach-Object { [int]$_ }) + $RuntimePorts | Sort-Object -Unique)
$RuntimeConfig = [pscustomobject]@{
    NodeName = 'worker-5070ti-01-runtime'; WindowsIP = $WindowsIP; ControlAddress = $ControlAddress
    Distro = 'Ubuntu-22.04'; OwnerSid = $OwnerSid
    Mappings = @($RuntimePorts | ForEach-Object { [pscustomobject]@{ ListenPort = $_; ConnectPort = $_ } })
}
$ExistingRuntimeTask = Get-ScheduledTask -TaskName $RuntimeTaskName -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $RuntimeConfigPath) {
    $Previous = Get-Content -LiteralPath $RuntimeConfigPath -Raw | ConvertFrom-Json
    if (($Previous | ConvertTo-Json -Depth 5 -Compress) -ne ($RuntimeConfig | ConvertTo-Json -Depth 5 -Compress)) {
        throw 'An existing runtime configuration differs from this additive deployment.'
    }
} elseif ($ExistingRuntimeTask) {
    throw 'Runtime task exists without the expected configuration.'
}
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}" -Apply -Watch' -f $ProxyScript, $RuntimeConfigPath
$RuntimeTaskOwnerSid = $null
if ($ExistingRuntimeTask) {
    $RuntimeTaskOwnerSid = [string]$ExistingRuntimeTask.Principal.UserId
    if ($RuntimeTaskOwnerSid -notmatch '^S-1-') {
        $RuntimeTaskOwnerSid = (New-Object Security.Principal.NTAccount($RuntimeTaskOwnerSid)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
}
if ($ExistingRuntimeTask -and ([string]$ExistingRuntimeTask.Principal.LogonType -ne 'Interactive' -or
    $RuntimeTaskOwnerSid -ne $OwnerSid -or
    @($ExistingRuntimeTask.Actions).Count -ne 1 -or $ExistingRuntimeTask.Actions[0].Execute -ne $PowerShellExe -or
    $ExistingRuntimeTask.Actions[0].Arguments -ne $Arguments)) {
    throw 'An existing runtime task differs from the proposed task.'
}
if (-not (Test-Path -LiteralPath $RuntimeConfigPath)) {
    $ProxyLines = @(& netsh.exe interface portproxy show v4tov4)
    foreach ($Port in $RuntimePorts) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -in @('0.0.0.0', '::', $WindowsIP) }).Count -gt 0) {
            throw "Runtime port $Port already has a Windows listener."
        }
        foreach ($Line in $ProxyLines) {
            $Fields = @($Line.Trim() -split '\s+')
            if ($Fields.Count -eq 4 -and $Fields[1] -eq [string]$Port -and $Fields[0] -in @('0.0.0.0', $WindowsIP)) {
                throw "Runtime port $Port already has a Windows mapping."
            }
        }
    }
}
$Mode = 'Inspect'
if ($Apply) { $Mode = 'Apply' }
[pscustomobject]@{
    Mode = $Mode; WindowsIP = $WindowsIP; AllowedSource = $ControlAddress
    PreservedScriptSHA256 = $ExpectedScriptHash; PreservedManagementTask = $OriginalTaskName
    NewRuntimeTask = $RuntimeTaskName; NewConfig = $RuntimeConfigPath; AddedPorts = $RuntimePorts
    ExpandedSourceBlockPorts = $NewBlockedPorts; NewAllowRule = $RuntimeAllowName
} | ConvertTo-Json -Depth 5
if (-not $Apply) { return }
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Requires the elevated WSL owner.' }
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
if ((Get-Item -LiteralPath $RuntimeRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked runtime directory rejected.' }
# Extend the deny boundary before opening any new runtime listener.
Set-NetFirewallRule -Name $BlockName -LocalPort @($NewBlockedPorts | ForEach-Object { [string]$_ }) | Out-Null
if (-not (Test-Path -LiteralPath $RuntimeConfigPath)) {
    $TempConfig = Join-Path $RuntimeRoot 'proxy-config.json.new'
    [IO.File]::WriteAllText($TempConfig, ($RuntimeConfig | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $TempConfig -Destination $RuntimeConfigPath
}
if ($ExistingRuntimeTask -and [string]$ExistingRuntimeTask.State -eq 'Running') {
    Write-Output 'RUNTIME_TASK_ALREADY_RUNNING=true; the management task was preserved.'
    return
}
# This is a one-shot call with its own state. It never stops the old keepalive.
& $ProxyScript -ConfigPath $RuntimeConfigPath -Apply
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity.Name
$TaskPrincipal = New-ScheduledTaskPrincipal -UserId $Identity.Name -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $RuntimeTaskName -Action $Action -Trigger $Trigger -Principal $TaskPrincipal `
    -Settings $Settings -Description 'Maintain four GPU Control runtime proxies using the existing approved local adapter.' -Force | Out-Null
Start-ScheduledTask -TaskName $RuntimeTaskName
Write-Output 'RUNTIME_PORTS_ADDED=8188,9100,9201,9301; management task and WSL were not restarted.'
