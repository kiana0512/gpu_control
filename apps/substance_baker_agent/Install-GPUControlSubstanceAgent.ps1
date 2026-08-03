[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:\GPUControl\agent',
    [string]$TaskNamePrefix = 'GPUControl-Substance-Baker-Agent',
    [ValidateRange(1, 16)][int]$InstanceCount = 4,
    [switch]$ConfirmNoActiveBakes
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'Invoke-GPUControlSubstanceAgent.ps1'
if (-not (Test-Path $source)) { throw "agent source missing: $source" }
$windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $windowsIdentity
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $windowsIdentity -LogonType Interactive -RunLevel Limited
$legacyTask = Get-ScheduledTask -TaskName $TaskNamePrefix -ErrorAction SilentlyContinue
$existingTasks = @()
if ($legacyTask) { $existingTasks += $legacyTask }
for ($instance = 1; $instance -le $InstanceCount; $instance++) {
    $taskName = '{0}-{1:D2}' -f $TaskNamePrefix, $instance
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) { $existingTasks += $existingTask }
}
if ($existingTasks.Count -gt 0 -and -not $ConfirmNoActiveBakes) {
    throw 'existing Baker tasks require -ConfirmNoActiveBakes after control-plane idle/fence verification'
}
foreach ($existingTask in $existingTasks) {
    Stop-ScheduledTask -TaskName $existingTask.TaskName -ErrorAction Stop
    if ($existingTask.State -eq 'Running') {
        $deadline = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 500
            $existingTask = Get-ScheduledTask -TaskName $existingTask.TaskName -ErrorAction Stop
        } while ($existingTask.State -eq 'Running' -and (Get-Date) -lt $deadline)
        if ($existingTask.State -eq 'Running') {
            throw "timed out stopping $($existingTask.TaskName); candidate was not installed"
        }
    }
}
if ($legacyTask) {
    Unregister-ScheduledTask -TaskName $TaskNamePrefix -Confirm:$false
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $InstallRoot 'Invoke-GPUControlSubstanceAgent.ps1') -Force
$installedTaskNames = @()
for ($instance = 1; $instance -le $InstanceCount; $instance++) {
    $taskName = '{0}-{1:D2}' -f $TaskNamePrefix, $instance
    $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        (Join-Path $InstallRoot 'Invoke-GPUControlSubstanceAgent.ps1') +
        '" -InstanceId ' + $instance + ' -InstanceCount ' + $InstanceCount
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    $installedTaskNames += $taskName
}
foreach ($taskName in $installedTaskNames) {
    Start-ScheduledTask -TaskName $taskName
    Write-Output "TASK=$taskName INSTALLED"
}
