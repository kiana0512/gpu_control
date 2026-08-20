[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\ProgramData\Li3D\MOFWorker',
    [string]$TaskName = 'GPUControl-MOF-Windows-Agent',
    [string]$RunAsUser = '',
    [switch]$ConfirmNoActiveMofJobs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$agentSource = Join-Path $PSScriptRoot 'Invoke-GPUControlMofAgent.ps1'
$runtimeSources = [ordered]@{
    'mof_unwrap.py' = Join-Path $PSScriptRoot 'mof_unwrap.py'
    'preflight_mof.py' = Join-Path $PSScriptRoot 'preflight_mof.py'
    'qa_uv.py' = Join-Path $PSScriptRoot 'qa_uv.py'
    'blender_uv_fbx_units.py' = Join-Path $PSScriptRoot 'blender_uv_fbx_units.py'
    'blender_uv_qa_adapter.py' = Join-Path $PSScriptRoot 'blender_uv_qa_adapter.py'
}
$blenderExe = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$caCertificate = Join-Path $InstallRoot 'secrets\GPU_CONTROL_LAN_CA.crt'
$secretFile = Join-Path $InstallRoot 'secrets\asset_worker_hmac_secret.txt'
$scriptsRoot = Join-Path $InstallRoot 'scripts'
$agentTarget = Join-Path $InstallRoot 'Invoke-GPUControlMofAgent.ps1'
$windowsIdentity = if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
    [Security.Principal.WindowsIdentity]::GetCurrent().Name
} else {
    $RunAsUser.Trim()
}
$runAsSid = (
    New-Object Security.Principal.NTAccount($windowsIdentity)
).Translate([Security.Principal.SecurityIdentifier]).Value

if (-not (Test-Path -LiteralPath $agentSource -PathType Leaf)) {
    throw "MOF agent source missing: $agentSource"
}
foreach ($source in $runtimeSources.Values) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "MOF runtime source missing: $source"
    }
}
foreach ($required in @($blenderExe, $caCertificate, $secretFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "required native Windows MOF dependency missing: $required"
    }
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and -not $ConfirmNoActiveMofJobs) {
    throw 'existing MOF task requires -ConfirmNoActiveMofJobs after control-plane idle verification'
}
if ($existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } while ($existingTask.State -eq 'Running' -and (Get-Date) -lt $deadline)
    if ($existingTask.State -eq 'Running') {
        throw 'timed out stopping the existing native Windows MOF Agent'
    }
}

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
New-Item -ItemType Directory -Path $scriptsRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallRoot 'jobs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallRoot 'logs') -Force | Out-Null
Copy-Item -LiteralPath $agentSource -Destination $agentTarget -Force
foreach ($entry in $runtimeSources.GetEnumerator()) {
    Copy-Item -LiteralPath ([string]$entry.Value) `
        -Destination (Join-Path $scriptsRoot ([string]$entry.Key)) -Force
}

foreach ($path in @($caCertificate, $secretFile, $agentTarget)) {
    & "$env:SystemRoot\System32\icacls.exe" $path /grant:r "*${runAsSid}:R" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to grant task identity read access: $path" }
}
foreach ($path in @($scriptsRoot)) {
    & "$env:SystemRoot\System32\icacls.exe" $path /grant:r `
        "*${runAsSid}:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to grant task identity script access: $path" }
}
foreach ($path in @((Join-Path $InstallRoot 'jobs'), (Join-Path $InstallRoot 'logs'))) {
    & "$env:SystemRoot\System32\icacls.exe" $path /grant:r `
        "*${runAsSid}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to grant task identity runtime access: $path" }
}

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $windowsIdentity
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($logonTrigger, $recoveryTrigger)
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
$principal = New-ScheduledTaskPrincipal -UserId $windowsIdentity `
    -LogonType Interactive -RunLevel Limited
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + `
    $agentTarget + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    $installedTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} while ($installedTask.State -ne 'Running' -and (Get-Date) -lt $deadline)
if ($installedTask.State -eq 'Running') {
    Start-Sleep -Seconds 4
    $installedTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
}
if ($installedTask.State -ne 'Running') {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    $lastResult = if ($taskInfo) {
        '0x{0:X8}' -f ([uint32]$taskInfo.LastTaskResult)
    } else { 'UNKNOWN' }
    throw "$TaskName did not stay Running after install; last_result=$lastResult"
}
Write-Output "TASK=$TaskName INSTALLED RUNNING MODE=NATIVE_WINDOWS USER=$windowsIdentity RECOVERY=1m"
