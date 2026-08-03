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
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $windowsIdentity
# A native console can return STATUS_CONTROL_C_EXIT (0xC000013A) when an
# interactive desktop, terminal, or console window is closed. Task Scheduler
# records that as a completed action and does not consistently apply
# RestartOnFailure. Re-offer the task every minute; IgnoreNew makes this a
# liveness trigger only and never creates a second Agent instance.
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($logonTrigger, $recoveryTrigger)
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
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
    $arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
        (Join-Path $InstallRoot 'Invoke-GPUControlSubstanceAgent.ps1') +
        '" -InstanceId ' + $instance + ' -InstanceCount ' + $InstanceCount
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Force | Out-Null
    $installedTaskNames += $taskName
}
foreach ($taskName in $installedTaskNames) {
    Start-ScheduledTask -TaskName $taskName
}
foreach ($taskName in $installedTaskNames) {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $installedTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    } while ($installedTask.State -ne 'Running' -and (Get-Date) -lt $deadline)
    if ($installedTask.State -eq 'Running') {
        Start-Sleep -Seconds 2
        $installedTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    }
    if ($installedTask.State -ne 'Running') {
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
        $lastResult = if ($taskInfo) { '0x{0:X8}' -f ([uint32]$taskInfo.LastTaskResult) } else { 'UNKNOWN' }
        throw "$taskName did not stay Running after install; last_result=$lastResult"
    }
    Write-Output "TASK=$taskName INSTALLED RUNNING RECOVERY=1m"
}
