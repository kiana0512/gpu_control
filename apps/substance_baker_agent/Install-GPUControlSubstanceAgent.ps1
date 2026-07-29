[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\GPUControl\agent',
    [string]$TaskNamePrefix = 'GPUControl-Substance-Baker-Agent',
    [ValidateRange(1, 16)][int]$InstanceCount = 4
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'Invoke-GPUControlSubstanceAgent.ps1'
if (-not (Test-Path $source)) { throw "agent source missing: $source" }
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $InstallRoot 'Invoke-GPUControlSubstanceAgent.ps1') -Force
$windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $windowsIdentity
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $windowsIdentity -LogonType Interactive -RunLevel Limited
$legacyTask = Get-ScheduledTask -TaskName $TaskNamePrefix -ErrorAction SilentlyContinue
if ($legacyTask) {
    Stop-ScheduledTask -TaskName $TaskNamePrefix -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskNamePrefix -Confirm:$false
}
for ($instance = 1; $instance -le $InstanceCount; $instance++) {
    $taskName = '{0}-{1:D2}' -f $TaskNamePrefix, $instance
    $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        (Join-Path $InstallRoot 'Invoke-GPUControlSubstanceAgent.ps1') +
        '" -InstanceId ' + $instance + ' -InstanceCount ' + $InstanceCount
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output "TASK=$taskName INSTALLED"
}
