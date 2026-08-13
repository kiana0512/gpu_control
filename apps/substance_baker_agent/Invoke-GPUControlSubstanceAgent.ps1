[CmdletBinding()]
param(
    [string]$ControlBaseUrl = 'https://10.3.34.11',
    [string]$CaCertificate = 'D:\GPUControl\secrets\GPU_CONTROL_LAN_CA.crt',
    [string]$SecretFile = 'D:\GPUControl\secrets\asset_worker_hmac_secret.txt',
    [string]$JobsRoot = 'D:\GPUControl\jobs',
    [string]$BakerExe = 'C:\Program Files\Adobe\Adobe Substance 3D Designer\substance3d_baker.exe',
    [string]$WslDistribution = 'Ubuntu-22.04',
    [string]$WslComfyContainer = 'gpu-control-node-comfyui-1',
    [ValidateRange(1, 16)][int]$InstanceId = 1,
    [ValidateRange(1, 16)][int]$InstanceCount = 4,
    [int]$PollSeconds = 2,
    [ValidateRange(10, 240)][int]$LeaseRenewalSeconds = 60,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$WorkerId = 'asset-worker-3090-b-windows-{0:D2}' -f $InstanceId
$NodeId = 'worker-3090-b'
$ExpectedBakerSha256 = '7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D'
$WslExe = 'C:\Windows\System32\wsl.exe'
$WslDockerExe = '/usr/bin/docker'
$CurlExe = 'C:\Windows\System32\curl.exe'
$CurrentJobs = 0
$ComfyContinuityError = 'SUBSTANCE_COMFYUI_CONTINUITY_FAILED'
$BakerTerminationError = 'SUBSTANCE_BAKER_TERMINATION_UNCONFIRMED'
$AgentInstanceId = [Guid]::NewGuid().ToString('N')
$AgentStartedAt = [DateTimeOffset]::UtcNow.ToString('o')
$AgentMutexName = "Global\GPUControl.SubstanceAgent.$WorkerId"
$AgentMutex = New-Object System.Threading.Mutex($false, $AgentMutexName)
$AgentMutexAcquired = $false

function Get-HexSha256Bytes([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-HmacSignature([string]$Method, [string]$Path, [string]$Body, [string]$Timestamp, [string]$Nonce) {
    $bodyBytes = [Text.Encoding]::UTF8.GetBytes($Body)
    $bodySha = Get-HexSha256Bytes $bodyBytes
    $message = "$($Method.ToUpperInvariant())`n$Path`n$Timestamp`n$Nonce`n$bodySha"
    $key = [Text.Encoding]::UTF8.GetBytes($script:WorkerSecret)
    $hmac = New-Object Security.Cryptography.HMACSHA256
    try {
        $hmac.Key = $key
        return ([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($message)))).Replace('-', '').ToLowerInvariant()
    }
    finally { $hmac.Dispose() }
}

function Invoke-SignedPost([string]$Path, [System.Collections.IDictionary]$Payload) {
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
    $nonce = [Guid]::NewGuid().ToString('N')
    $signature = Get-HmacSignature 'POST' $Path $body $timestamp $nonce
    $bodyFile = Join-Path $env:TEMP ("gpu-control-body-" + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        [IO.File]::WriteAllText($bodyFile, $body, (New-Object Text.UTF8Encoding($false)))
        $response = & $CurlExe --silent --show-error --fail --cacert $CaCertificate --ssl-no-revoke `
            --connect-timeout 5 --max-time 20 `
            -X POST ($ControlBaseUrl + $Path) `
            -H 'Content-Type: application/json' `
            -H ("X-Asset-Timestamp: $timestamp") `
            -H ("X-Asset-Nonce: $nonce") `
            -H ("X-Asset-Signature: $signature") `
            --data-binary ("@$bodyFile")
        if ($LASTEXITCODE -ne 0) { throw "signed POST failed: $Path ($LASTEXITCODE)" }
        return ($response | ConvertFrom-Json)
    }
    finally { Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue }
}

function Invoke-LeasedJsonPost([string]$Path, [string]$Lease, [System.Collections.IDictionary]$Payload) {
    $body = $Payload | ConvertTo-Json -Depth 8 -Compress
    $bodyFile = Join-Path $env:TEMP ("gpu-control-lease-" + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        [IO.File]::WriteAllText($bodyFile, $body, (New-Object Text.UTF8Encoding($false)))
        $response = & $CurlExe --silent --show-error --fail --cacert $CaCertificate --ssl-no-revoke `
            --connect-timeout 5 --max-time 20 `
            -X POST ($ControlBaseUrl + $Path) -H 'Content-Type: application/json' `
            -H ("X-Asset-Lease: $Lease") --data-binary ("@$bodyFile")
        if ($LASTEXITCODE -ne 0) { throw "leased POST failed: $Path ($LASTEXITCODE)" }
        return ($response | ConvertFrom-Json)
    }
    finally { Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue }
}

function Get-BakerHostProcessEvidence {
    $checkedAt = [DateTimeOffset]::UtcNow.ToString('o')
    try {
        # This is deliberately a host-wide process query, not the Agent's
        # in-memory CurrentJobs counter.  After an Agent restart an orphaned
        # native Baker is still visible here and keeps recovery fail-closed.
        $processes = @(Get-CimInstance -ClassName Win32_Process `
            -Filter "Name = 'substance3d_baker.exe'" -ErrorAction Stop)
        return [ordered]@{
            status = 'HEALTHY'
            active_processes = [int]$processes.Count
            checked_at = $checkedAt
        }
    }
    catch {
        # Never turn an unavailable process provider into an asserted zero.
        # The Asset API marks this Worker draining and will not release a
        # recovery fence until a later HEALTHY host probe explicitly reports 0.
        return [ordered]@{
            status = 'FAILED'
            active_processes = $null
            checked_at = $checkedAt
        }
    }
}

function Send-Heartbeat {
    $os = Get-CimInstance Win32_OperatingSystem
    $availableMb = [int][Math]::Floor([double]$os.FreePhysicalMemory / 1024)
    $processEvidence = Get-BakerHostProcessEvidence
    $payload = [ordered]@{
        worker_id = $WorkerId; node_id = $NodeId
        display_name = ('3090-B Windows Substance Baker #{0:D2}' -f $InstanceId); hostname = $env:COMPUTERNAME
        blender_version = 'substance-15.1.0'; skill_version = 'substance-baker-2026.08.12-v7'
        cpu_count = [Environment]::ProcessorCount; max_concurrency = 1
        current_jobs = $script:CurrentJobs; load_1m = 0; available_memory_mb = $availableMb
        agent_instance_id = $AgentInstanceId; agent_started_at = $AgentStartedAt
        substance_process_probe_status = [string]$processEvidence.status
        substance_process_probe_checked_at = [string]$processEvidence.checked_at
        substance_active_processes = $processEvidence.active_processes
    }
    $null = Invoke-SignedPost '/internal/v1/assets/workers/heartbeat' $payload
}

function Assert-ComfyUiProcessStable([string]$ExpectedIdentity = '') {
    # The control plane has already drained 3090-B and waited for its current
    # prompt to finish before a Baker can claim. Keep the healthy container
    # process alive so cache eviction never masquerades as a service restart.
    $probeLines = @(& $WslExe -d $WslDistribution -u gpucontrol -- $WslDockerExe inspect `
        --format '{{.Id}}~{{.State.StartedAt}}~{{.RestartCount}}~{{.State.Status}}~{{.State.Health.Status}}' `
        $WslComfyContainer 2>$null | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ })
    $probeExitCode = $LASTEXITCODE
    $identity = @($probeLines | Where-Object {
        $_ -match '^[0-9a-f]{64}~[^~]+~[0-9]+~running~healthy$'
    } | Select-Object -Last 1)
    if ($probeExitCode -ne 0 -or $identity.Count -ne 1) {
        throw "${ComfyContinuityError}: ComfyUI identity/health probe failed: $($probeLines -join '; ')"
    }
    $identityToken = [string]$identity[0]
    if ($ExpectedIdentity -and $identityToken -ne $ExpectedIdentity) {
        throw "${ComfyContinuityError}: ComfyUI process changed during native Baker fence: expected=$ExpectedIdentity actual=$identityToken"
    }
    return $identityToken
}

function Clear-ComfyUiModelsForBaker([string]$ExpectedIdentity) {
    $python = @'
import json
import time
import urllib.request

base = "http://127.0.0.1:8188"
with urllib.request.urlopen(base + "/queue", timeout=5) as response:
    queue = json.load(response)
running = queue.get("queue_running", [])
pending = queue.get("queue_pending", [])
if running or pending:
    raise SystemExit("COMFY_QUEUE_NOT_EMPTY")
request = urllib.request.Request(
    base + "/free",
    data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    response.read()

# Model eviction is asynchronous. Keep the queue fenced while waiting for
# ComfyUI's own allocator evidence to reach the Baker safety threshold.
deadline = time.monotonic() + 30
while True:
    with urllib.request.urlopen(base + "/queue", timeout=5) as response:
        queue = json.load(response)
    if queue.get("queue_running", []) or queue.get("queue_pending", []):
        raise SystemExit("COMFY_QUEUE_NOT_EMPTY")
    with urllib.request.urlopen(base + "/system_stats", timeout=5) as response:
        stats = json.load(response)
    devices = stats.get("devices") or []
    if not devices:
        raise SystemExit("COMFY_VRAM_EVIDENCE_MISSING")
    total_mb = float(devices[0].get("vram_total", 0)) / 1048576
    free_mb = float(devices[0].get("vram_free", 0)) / 1048576
    ratio = free_mb / total_mb if total_mb > 0 else 0
    if free_mb >= 6144 and ratio >= 0.60:
        break
    if time.monotonic() >= deadline:
        raise SystemExit("COMFY_VRAM_RECOVERY_UNSAFE")
    time.sleep(0.5)
print(json.dumps({
    "queue_empty": True,
    "models_unloaded": True,
    "free_vram_mb": round(free_mb),
    "total_vram_mb": round(total_mb),
    "free_ratio": round(ratio, 4),
}, separators=(",", ":")))
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
    $pythonCommand = "import base64;exec(base64.b64decode('$encoded'))"
    $probeLines = @(& $WslExe -d $WslDistribution -u gpucontrol -- $WslDockerExe exec -i `
        $WslComfyContainer /opt/python/bin/python3 -c $pythonCommand 2>&1 | ForEach-Object {
            $_.ToString().Trim()
        } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) {
        throw "${ComfyContinuityError}: ComfyUI queue drain/model eviction failed: $($probeLines -join '; ')"
    }
    $evidenceLine = @($probeLines | Where-Object { $_ -match '^\{.*\}$' } | Select-Object -Last 1)
    if ($evidenceLine.Count -ne 1) {
        throw "${ComfyContinuityError}: ComfyUI VRAM recovery evidence missing"
    }
    $script:ComfyDrainEvidence = [string]$evidenceLine[0]
    $null = Assert-ComfyUiProcessStable $ExpectedIdentity
}

function Enter-BakerGpuFence([string]$JobId) {
    # The durable, multi-worker fence is owned by Asset API in PostgreSQL.
    # Keep only this attempt's ComfyUI identity locally so a dead process or
    # host reboot cannot leave a stale shared file that poisons later claims.
    $identity = Assert-ComfyUiProcessStable
    Clear-ComfyUiModelsForBaker $identity
    return $identity
}

function Exit-BakerGpuFence([string]$JobId, [string]$ExpectedIdentity) {
    $null = Assert-ComfyUiProcessStable $ExpectedIdentity
}

function Assert-FileHash([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) { throw "SHA-256 mismatch: $Path" }
}

function Get-OptionalProperty($Object, [string]$Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function ConvertTo-NativeProcessArgument([string]$Value) {
    if ($Value.IndexOf([char]0) -ge 0) { throw 'native process argument contains a NUL byte' }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }

    # Start-Process ultimately constructs one Windows command line.  Quote
    # every argument with whitespace using the CommandLineToArgvW escaping
    # rules so input/output paths cannot be split into additional arguments.
    $quoted = New-Object Text.StringBuilder
    [void]$quoted.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            [void]$quoted.Append([char]92, (($backslashes * 2) + 1))
            [void]$quoted.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$quoted.Append([char]92, $backslashes)
            $backslashes = 0
        }
        [void]$quoted.Append($character)
    }
    if ($backslashes -gt 0) { [void]$quoted.Append([char]92, ($backslashes * 2)) }
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

function Invoke-BakerLeaseRenewal(
    [string]$JobId,
    [string]$Lease,
    [double]$Progress,
    [string]$Stage,
    [string]$Message,
    [int]$EstimatedRemainingSeconds
) {
    $lastFailure = ''
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            return Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$JobId/progress" $Lease ([ordered]@{
                progress = $Progress; stage = $Stage; message = $Message
                estimated_remaining_seconds = $EstimatedRemainingSeconds
            })
        }
        catch {
            $lastFailure = $_.Exception.Message
            if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
        }
    }
    throw "Substance Baker lease renewal failed after 3 attempts: $lastFailure"
}

function Get-FileSha256WithLeaseRenewal(
    [string]$Path,
    [string]$JobId,
    [string]$Lease,
    [double]$Progress,
    [string]$Stage,
    [string]$Message,
    [int]$EstimatedRemainingSeconds
) {
    # Get-FileHash is synchronous and can outlive a lease for multi-gigabyte
    # artifacts.  Hash in bounded chunks so the same job remains exclusively
    # owned while local output evidence is produced.
    $renewal = Invoke-BakerLeaseRenewal $JobId $Lease $Progress $Stage $Message $EstimatedRemainingSeconds
    if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
        throw 'Substance Baker stopped because the job was cancelled while hashing artifacts'
    }
    try { Send-Heartbeat } catch { Write-Warning $_ }

    $stream = $null
    $sha = $null
    $crypto = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $sha = [Security.Cryptography.SHA256]::Create()
        $crypto = New-Object Security.Cryptography.CryptoStream(
            [IO.Stream]::Null,
            $sha,
            [Security.Cryptography.CryptoStreamMode]::Write
        )
        $buffer = New-Object byte[] (4 * 1024 * 1024)
        $nextRenewal = [DateTimeOffset]::UtcNow.AddSeconds($LeaseRenewalSeconds)
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $crypto.Write($buffer, 0, $read)
            if ([DateTimeOffset]::UtcNow -ge $nextRenewal) {
                $renewal = Invoke-BakerLeaseRenewal $JobId $Lease $Progress $Stage $Message $EstimatedRemainingSeconds
                if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                    throw 'Substance Baker stopped because the job was cancelled while hashing artifacts'
                }
                try { Send-Heartbeat } catch { Write-Warning $_ }
                $nextRenewal = [DateTimeOffset]::UtcNow.AddSeconds($LeaseRenewalSeconds)
            }
        }
        $crypto.FlushFinalBlock()
        return ([BitConverter]::ToString($sha.Hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $crypto) { $crypto.Dispose() }
        if ($null -ne $sha) { $sha.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Stop-BakerProcess($Process) {
    if ($null -eq $Process) { return $true }
    try {
        $Process.Refresh()
        if ($Process.HasExited) { return $true }
        $Process.Kill()
    }
    catch {
        throw "${BakerTerminationError}: Kill() failed: $($_.Exception.Message)"
    }
    try {
        if (-not $Process.WaitForExit(10000)) {
            throw "${BakerTerminationError}: process did not exit within 10 seconds after Kill()"
        }
        # Refresh and re-read HasExited instead of treating WaitForExit's
        # return value alone as proof.  An unverified native process must keep
        # the server-side physical-GPU recovery fence installed.
        $Process.Refresh()
        if (-not $Process.HasExited) {
            throw "${BakerTerminationError}: process remained active after WaitForExit()"
        }
    }
    catch {
        if ($_.Exception.Message.StartsWith($BakerTerminationError)) { throw }
        throw "${BakerTerminationError}: exit verification failed: $($_.Exception.Message)"
    }
    return $true
}

function Stop-CompletionUploadProcess($Process) {
    if ($null -eq $Process) { return }
    if (-not $Process.HasExited) {
        $Process.Kill()
        if (-not $Process.WaitForExit(10000)) {
            throw 'curl completion upload process did not terminate after Kill()'
        }
    }
}

function Assert-BakerCommandResult($ExitCode, [string[]]$OutputLines) {
    $successMarkerPresent = (($OutputLines -join "`n") -match 'Bake finished successfully')

    # Windows PowerShell 5.1 can occasionally expose a null ExitCode for an
    # already completed Start-Process handle.  A real, observable non-zero
    # value always fails closed.  A null value is accepted only when Baker's
    # own completion marker is present in the fully flushed output.
    if ($null -ne $ExitCode -and [int]$ExitCode -ne 0) {
        throw "Substance Baker exited with code $ExitCode"
    }
    if (-not $successMarkerPresent) {
        if ($null -eq $ExitCode) {
            throw 'Substance Baker exit code unavailable and success marker missing'
        }
        throw 'Substance Baker success marker missing'
    }
    $exitCodeObserved = ($null -ne $ExitCode)
    $normalizedExitCode = if ($exitCodeObserved) { [int]$ExitCode } else { $null }
    return [ordered]@{
        exit_code_observed = $exitCodeObserved
        exit_code = $normalizedExitCode
        success_marker_present = $true
    }
}

function Invoke-BakerCommand(
    [string[]]$Arguments,
    [string]$LogPath,
    [string]$JobId,
    [string]$Lease,
    [double]$Progress,
    [string]$Stage,
    [string]$Message,
    [int]$EstimatedRemainingSeconds
) {
    # Substance writes ordinary INFO records to stderr.  With the agent-wide
    # ErrorActionPreference=Stop, PowerShell 5.1 otherwise promotes the first
    # INFO line to a terminating NativeCommandError and kills a healthy bake.
    # Run it as an asynchronous native process instead.  This also leaves the
    # agent free to renew the 300-second control-plane lease and heartbeat while
    # a large bake is still using the GPU.
    $stdoutPath = Join-Path $env:TEMP ("gpu-control-baker-stdout-" + [Guid]::NewGuid().ToString('N') + '.log')
    $stderrPath = Join-Path $env:TEMP ("gpu-control-baker-stderr-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    $exitCode = $null
    $lines = @()
    try {
        $argumentLine = (($Arguments | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $BakerExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

        while (-not $process.WaitForExit($LeaseRenewalSeconds * 1000)) {
            $renewal = Invoke-BakerLeaseRenewal $JobId $Lease $Progress $Stage $Message $EstimatedRemainingSeconds
            if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                Stop-BakerProcess $process
                throw 'Substance Baker stopped because the job was cancelled'
            }
            try { Send-Heartbeat }
            catch {
                "heartbeat warning during $Stage`: $($_.Exception.Message)" | Add-Content -LiteralPath $LogPath -Encoding UTF8
                Write-Warning $_
            }
        }
        # Ensure asynchronous output redirection is completely flushed before
        # the log files are read and the process handle is disposed.
        $process.WaitForExit()
        $process.Refresh()
        $exitCode = $process.ExitCode
    }
    catch {
        # Preserve the original execution failure only after native-process
        # termination is positively verified.  Stop-BakerProcess throws the
        # dedicated recovery-required error when Kill/WaitForExit/HasExited
        # cannot prove that the GPU process is gone.
        $commandFailureMessage = $_.Exception.Message
        $null = Stop-BakerProcess $process
        throw $commandFailureMessage
    }
    finally {
        foreach ($processLog in @($stdoutPath, $stderrPath)) {
            if (Test-Path -LiteralPath $processLog) {
                $lines += @(Get-Content -LiteralPath $processLog -ErrorAction SilentlyContinue | ForEach-Object { $_.ToString() })
                Remove-Item -LiteralPath $processLog -Force -ErrorAction SilentlyContinue
            }
        }
        if ($null -ne $process) { $process.Dispose() }
        if ($lines.Count -gt 0) { $lines | Add-Content -LiteralPath $LogPath -Encoding UTF8 }
    }
    $verified = Assert-BakerCommandResult -ExitCode $exitCode -OutputLines $lines
    $outputName = $null
    $outputNameIndex = [Array]::IndexOf([object[]]$Arguments, '--output_name')
    if ($outputNameIndex -ge 0 -and ($outputNameIndex + 1) -lt $Arguments.Count) {
        $outputName = [string]$Arguments[$outputNameIndex + 1]
    }
    $bakerName = if ($Arguments.Count -gt 1) { [string]$Arguments[1] } else { $null }
    return [ordered]@{
        baker = $bakerName
        output_name = $outputName
        exit_code_observed = [bool]$verified.exit_code_observed
        exit_code = $verified.exit_code
        success_marker_present = [bool]$verified.success_marker_present
    }
}

function Invoke-LeasedMultipartUpload(
    [string[]]$Arguments,
    [string]$JobId,
    [string]$Lease,
    [string]$LogPath
) {
    # FastAPI parses the complete multipart body before the completion handler
    # can renew the lease.  Keep curl asynchronous and renew through the
    # independent progress endpoint for the whole transfer and server-side
    # validation window.
    $stdoutPath = Join-Path $env:TEMP ("gpu-control-upload-stdout-" + [Guid]::NewGuid().ToString('N') + '.json')
    $stderrPath = Join-Path $env:TEMP ("gpu-control-upload-stderr-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    $exitCode = $null
    $stdoutText = ''
    $stderrText = ''
    try {
        $renewal = Invoke-BakerLeaseRenewal $JobId $Lease 95 'UPLOADING_ARTIFACTS' `
            'Uploading and validating final Substance artifacts' 120
        if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
            throw 'Substance result upload was not started because the job was cancelled'
        }
        try { Send-Heartbeat }
        catch {
            "heartbeat warning during UPLOADING_ARTIFACTS: $($_.Exception.Message)" | `
                Add-Content -LiteralPath $LogPath -Encoding UTF8
            Write-Warning $_
        }

        $argumentLine = (($Arguments | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $CurlExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

        while (-not $process.WaitForExit($LeaseRenewalSeconds * 1000)) {
            try {
                $renewal = Invoke-BakerLeaseRenewal $JobId $Lease 95 'UPLOADING_ARTIFACTS' `
                    'Uploading and validating final Substance artifacts' 120
            }
            catch {
                # The completion transaction can clear the lease just before
                # curl receives its successful response.  Give that response a
                # short bounded opportunity to win this race.
                if ($process.WaitForExit(2000)) { break }
                throw
            }
            if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                Stop-CompletionUploadProcess $process
                throw 'Substance result upload stopped because the job was cancelled'
            }
            try { Send-Heartbeat }
            catch {
                "heartbeat warning during UPLOADING_ARTIFACTS: $($_.Exception.Message)" | `
                    Add-Content -LiteralPath $LogPath -Encoding UTF8
                Write-Warning $_
            }
        }
        $process.WaitForExit()
        $process.Refresh()
        $exitCode = $process.ExitCode
    }
    catch {
        Stop-CompletionUploadProcess $process
        throw
    }
    finally {
        if (Test-Path -LiteralPath $stdoutPath) {
            $stdoutText = [IO.File]::ReadAllText($stdoutPath)
            Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stderrPath) {
            $stderrText = [IO.File]::ReadAllText($stderrPath)
            Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
        }
        if ($null -ne $process) { $process.Dispose() }
    }

    if ($null -eq $exitCode) {
        throw 'result upload failed: curl exit code was unavailable'
    }
    if ([int]$exitCode -ne 0) {
        $failureTail = if ($stderrText.Length -gt 2000) {
            $stderrText.Substring($stderrText.Length - 2000)
        } else { $stderrText }
        throw "result upload failed: curl exited with code ${exitCode}: $failureTail"
    }
    try { $completion = $stdoutText | ConvertFrom-Json }
    catch { throw "result upload returned invalid JSON: $($_.Exception.Message)" }
    if ($null -eq $completion) { throw 'result upload returned an empty response' }

    $status = [string](Get-OptionalProperty $completion 'status')
    $accepted = [bool](Get-OptionalProperty $completion 'accepted')
    $cancelRequested = [bool](Get-OptionalProperty $completion 'cancel_requested')
    if ($status -eq 'SUCCEEDED' -and $accepted) { return $completion }
    if ($status -eq 'CANCELLED' -and -not $accepted -and $cancelRequested) {
        return $completion
    }
    throw "result upload response was not terminally accepted: status=$status accepted=$accepted cancel_requested=$cancelRequested"
}

function Execute-Bake($Job) {
    $jobId = [string]$Job.job_id
    $lease = [string]$Job.lease_token
    $generation = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
    $jobRoot = Join-Path $JobsRoot "$jobId\$generation"
    $inputZip = Join-Path $jobRoot 'substance_bake_input.zip'
    $inputRoot = Join-Path $jobRoot 'payload'
    $outputRoot = Join-Path $jobRoot 'output'
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    $fenceEntered = $false
    $comfyProcessIdentity = ''
    $comfyProcessContinuityVerified = $false
    try {
        & $CurlExe --silent --show-error --fail --cacert $CaCertificate --ssl-no-revoke `
            -H ("X-Asset-Lease: $lease") -o $inputZip ($ControlBaseUrl + [string]$Job.input_url)
        if ($LASTEXITCODE -ne 0) { throw 'input download failed' }
        Assert-FileHash $inputZip ([string]$Job.input_sha256)
        Expand-Archive -LiteralPath $inputZip -DestinationPath $inputRoot -Force
        $request = Get-Content -LiteralPath (Join-Path $inputRoot 'request.json') -Raw | ConvertFrom-Json
        if ($request.job_type -ne 'SUBSTANCE_BAKE_V1') { throw 'invalid Baker request type' }
        foreach ($role in @('low', 'high', 'cage', 'base_color', 'roughness', 'metallic')) {
            $name = Get-OptionalProperty $request.files $role
            if ($name) {
                $path = Join-Path (Join-Path $inputRoot 'input') ([string]$name)
                Assert-FileHash $path ([string](Get-OptionalProperty $request.input_sha256 $role))
            }
        }
        Assert-FileHash $BakerExe $ExpectedBakerSha256
        $version = (& $BakerExe --version 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '15\.1\.0') { throw 'Baker version mismatch' }

        $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
            progress = 5; stage = 'GPU_FENCING'; message = '3090-B inference queue drained; ComfyUI models unloaded and VRAM recovery verified before native Windows Baker runs'; estimated_remaining_seconds = 540
        })
        $comfyProcessIdentity = Enter-BakerGpuFence $jobId
        $fenceEntered = $true
        $script:CurrentJobs = 1
        Send-Heartbeat

        $profile = [string]$request.options.profile
        $resolution = [int]$request.options.resolution
        $cache = [int]$request.options.texture_cache_mb
        $low = Join-Path (Join-Path $inputRoot 'input') ([string]$request.files.low)
        $highName = Get-OptionalProperty $request.files 'high'
        $cageName = Get-OptionalProperty $request.files 'cage'
        $high = if ($highName) { Join-Path (Join-Path $inputRoot 'input') ([string]$highName) } else { $null }
        $cage = if ($cageName) { Join-Path (Join-Path $inputRoot 'input') ([string]$cageName) } else { $null }
        $logPath = Join-Path $outputRoot 'baker.log'
        $commandEvidence = @()
        "GPU Control job=$jobId profile=$profile" | Set-Content -LiteralPath $logPath -Encoding UTF8
        # Substance 3D Baker 15.1 declares output_size as QPoint and accepts
        # the two dimensions as a comma-separated uint pair (for example
        # "512,512"), not the more common "512x512" spelling.
        $sizeArg = "${resolution},${resolution}"

        if ($profile -in @('ao-self-v1', 'pbr-core-v1')) {
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
                progress = 20; stage = 'BAKING_AO'; message = 'SAL + SoRa is generating ambient occlusion'; estimated_remaining_seconds = 420
            })
            $args = @('--verbose', 'AmbientOcclusion.Raytraced', '--inputs', $low,
                '--use_lowdef_as_highdef', 'true', '--backends', 'SAL,SoRa',
                '--output_path', $outputRoot, '--output_name', 'asset_ao', '--output_format', 'png',
                '--output_size', $sizeArg, '--secondary.sample_count', '64',
                '--projection.sampling_rate', '2x2', '--texture_cache_size', $cache.ToString())
            if ($cage) { $args += @('--use_cage', 'true', '--cage_scene_path', $cage) }
            $commandEvidence += @(Invoke-BakerCommand $args $logPath $jobId $lease 20 `
                'BAKING_AO' 'SAL + SoRa is generating ambient occlusion' 420)
        }
        if ($profile -in @('normal-dx-v1', 'pbr-core-v1')) {
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
                progress = 55; stage = 'BAKING_NORMAL'; message = 'SAL + SoRa is generating a DirectX tangent-space normal map'; estimated_remaining_seconds = 300
            })
            $args = @('--verbose', 'Normal.Raytraced', '--inputs', $low, '--high_scene_paths', $high,
                '--backends', 'SAL,SoRa', '--output_path', $outputRoot, '--output_name', 'asset_normal_dx',
                '--output_format', 'png', '--output_size', $sizeArg,
                '--projection.mesh_match_mode', 'match_mesh_name', '--name_suffix_low', '_low',
                '--name_suffix_high', '_high', '--projection.normalized_distance', 'false',
                '--projection.max_height', '0.05', '--projection.max_depth', '0.05',
                '--projection.sampling_rate', '4x4', '--output_texture_orientation', 'directx',
                '--output_texture_space', 'tangent_space', '--texture_cache_size', $cache.ToString())
            if ($cage) { $args += @('--use_cage', 'true', '--cage_scene_path', $cage) }
            $commandEvidence += @(Invoke-BakerCommand $args $logPath $jobId $lease 55 `
                'BAKING_NORMAL' 'SAL + SoRa is generating a DirectX tangent-space normal map' 300)
        }

        if ($profile -eq 'li3d-pbr-full-v2') {
            $commonProjection = @('--inputs', $low, '--high_scene_paths', $high,
                '--backends', 'SAL,SoRa', '--output_path', $outputRoot,
                '--output_format', 'png', '--output_size', $sizeArg,
                '--projection.mesh_match_mode', 'match_all',
                '--projection.normalized_distance', 'true',
                '--projection.max_height', '0.05', '--projection.max_depth', '0.05',
                '--projection.sampling_rate', '4x4', '--texture_cache_size', $cache.ToString())
            if ($cage) { $commonProjection += @('--use_cage', 'true', '--cage_scene_path', $cage) }

            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
                progress = 15; stage = 'BAKING_TEXTURE_TRANSFER'; message = 'Projecting Base Color, Roughness, and Metallic'; estimated_remaining_seconds = 480
            })
            foreach ($textureRole in @('base_color', 'roughness', 'metallic')) {
                $sourceName = Get-OptionalProperty $request.files $textureRole
                if (-not $sourceName) { throw "missing source texture: $textureRole" }
                $sourcePath = Join-Path (Join-Path $inputRoot 'input') ([string]$sourceName)
                $args = @('--verbose', 'TextureTransfer.Raytraced') + $commonProjection + @(
                    '--output_name', "asset_$textureRole", '--source_texture_path', $sourcePath,
                    '--filtering_mode', 'bilinear', '--padding_radius', '16')
                $commandEvidence += @(Invoke-BakerCommand $args $logPath $jobId $lease 15 `
                    'BAKING_TEXTURE_TRANSFER' 'Projecting Base Color, Roughness, and Metallic' 480)
            }

            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
                progress = 40; stage = 'BAKING_GEOMETRY_MAPS'; message = 'Generating AO, Curvature, Thickness, and Position maps'; estimated_remaining_seconds = 330
            })
            $geometryBakes = @(
                @('AmbientOcclusion.Raytraced', 'asset_ao'),
                @('Curvature.Raytraced', 'asset_curvature'),
                @('Thickness.Raytraced', 'asset_thickness'),
                @('Position.Raytraced', 'asset_position')
            )
            foreach ($bake in $geometryBakes) {
                $args = @('--verbose', $bake[0]) + $commonProjection + @('--output_name', $bake[1])
                $commandEvidence += @(Invoke-BakerCommand $args $logPath $jobId $lease 40 `
                    'BAKING_GEOMETRY_MAPS' 'Generating AO, Curvature, Thickness, and Position maps' 330)
            }

            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/progress" $lease ([ordered]@{
                progress = 72; stage = 'BAKING_NORMALS'; message = 'Generating DirectX, OpenGL, and world-space normal maps'; estimated_remaining_seconds = 180
            })
            foreach ($normalSpec in @(
                @('asset_normal_dx', 'tangent_space', 'directx'),
                @('asset_normal_gl', 'tangent_space', 'opengl'),
                @('asset_world_normal', 'world_space', 'opengl')
            )) {
                $args = @('--verbose', 'Normal.Raytraced') + $commonProjection + @(
                    '--output_name', $normalSpec[0], '--output_texture_space', $normalSpec[1],
                    '--output_texture_orientation', $normalSpec[2])
                $commandEvidence += @(Invoke-BakerCommand $args $logPath $jobId $lease 72 `
                    'BAKING_NORMALS' 'Generating DirectX, OpenGL, and world-space normal maps' 180)
            }
        }

        $hashes = [ordered]@{}
        foreach ($outputRole in @('base_color', 'roughness', 'metallic', 'ao', 'normal_dx', 'normal_gl', 'world_normal', 'curvature', 'thickness', 'position')) {
            $outputFile = Join-Path $outputRoot "asset_$outputRole.png"
            if (Test-Path $outputFile) {
                $hashes[$outputRole] = Get-FileSha256WithLeaseRenewal $outputFile $jobId $lease `
                    94.8 'HASHING_ARTIFACTS' 'Hashing final Substance artifacts' 120
            }
        }

        Exit-BakerGpuFence $jobId $comfyProcessIdentity
        $fenceEntered = $false
        $comfyProcessContinuityVerified = $true
        $resultPath = Join-Path $outputRoot 'baker_result.json'
        $allExitCodesObserved = @(
            $commandEvidence | Where-Object { -not [bool]$_.exit_code_observed }
        ).Count -eq 0
        $summaryExitCode = if ($allExitCodesObserved) { 0 } else { $null }
        $resultJson = [ordered]@{
            schema_version = 2; job_id = $jobId; status = 'SUCCEEDED'; profile = $profile
            tool = [ordered]@{ version = '15.1.0'; exe_sha256 = $ExpectedBakerSha256 }
            execution = [ordered]@{
                exit_code = $summaryExitCode
                exit_code_observed = $allExitCodesObserved
                success_marker_verified = $true
                command_count = $commandEvidence.Count
                commands = $commandEvidence
                gpu_backends = @('SAL', 'SoRa')
                gpu_uuid = 'GPU-092a5184-5857-d196-5df2-efa9503368aa'
                comfyui_cache_policy = 'queue_drained_models_unloaded_vram_verified'
                comfyui_drain_evidence = $script:ComfyDrainEvidence
                comfyui_container_restarted = $false
                comfyui_process_continuity_verified = $comfyProcessContinuityVerified
            }
            output_sha256 = $hashes
        } | ConvertTo-Json -Depth 8
        # Windows PowerShell 5.1 Set-Content -Encoding UTF8 writes a BOM.  The
        # control plane validates strict UTF-8 JSON, so write explicitly BOMless.
        [IO.File]::WriteAllText($resultPath, $resultJson, (New-Object Text.UTF8Encoding($false)))

        $curlArgs = @('--silent', '--show-error', '--fail', '--cacert', $CaCertificate, '--ssl-no-revoke',
            '--connect-timeout', '5',
            '-X', 'POST', ($ControlBaseUrl + "/internal/v1/assets/jobs/$jobId/substance-complete"),
            '-H', "X-Asset-Lease: $lease", '-F', "result=@$resultPath;type=application/json",
            '-F', "log=@$logPath;type=text/plain")
        foreach ($outputRole in $hashes.Keys) {
            $curlArgs += @('-F', "$outputRole=@$(Join-Path $outputRoot "asset_$outputRole.png");type=image/png")
        }
        $null = Invoke-LeasedMultipartUpload $curlArgs $jobId $lease $logPath
    }
    catch {
        $failureMessage = $_.Exception.Message
        $cacheContinuityFailure = $failureMessage.StartsWith($ComfyContinuityError)
        $terminationUnconfirmed = $failureMessage.StartsWith($BakerTerminationError)
        if ($fenceEntered) {
            if (-not $terminationUnconfirmed) {
                try {
                    Exit-BakerGpuFence $jobId $comfyProcessIdentity
                    $fenceEntered = $false
                }
                catch {
                    $cacheContinuityFailure = $true
                    $failureMessage = $_.Exception.Message
                    Write-Error -ErrorAction Continue $_
                }
            }
        }
        try {
            $failureCode = if ($terminationUnconfirmed) {
                $BakerTerminationError
            }
            elseif ($cacheContinuityFailure) {
                $ComfyContinuityError
            }
            else {
                'SUBSTANCE_EXECUTION_FAILED'
            }
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/fail" $lease ([ordered]@{
                code = $failureCode
                message = $failureMessage.Substring(0, [Math]::Min(3900, $failureMessage.Length))
                retryable = -not ($cacheContinuityFailure -or $terminationUnconfirmed)
            })
        } catch { Write-Error $_ }
        throw $failureMessage
    }
    finally {
        $script:CurrentJobs = 0
        try { Send-Heartbeat } catch { Write-Warning $_ }
    }
}

try {
    try {
        $AgentMutexAcquired = $AgentMutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        # The prior Agent process is gone, so mutex ownership transfers here.
        # A surviving native Baker is still caught by the host-wide probe.
        $AgentMutexAcquired = $true
    }
    if (-not $AgentMutexAcquired) {
        throw "SUBSTANCE_AGENT_INSTANCE_ALREADY_RUNNING: $WorkerId"
    }

    if (-not (Test-Path -LiteralPath $CurlExe)) { throw "curl.exe missing: $CurlExe" }
    if (-not (Test-Path -LiteralPath $CaCertificate)) { throw "CA certificate missing: $CaCertificate" }
    if (-not (Test-Path -LiteralPath $SecretFile)) { throw "worker secret missing: $SecretFile" }
    if (-not (Test-Path -LiteralPath $BakerExe)) { throw "Baker missing: $BakerExe" }
    $script:WorkerSecret = (Get-Content -LiteralPath $SecretFile -Raw).Trim()
    if ($script:WorkerSecret.Length -lt 32) { throw 'worker secret is too short' }
    New-Item -ItemType Directory -Path $JobsRoot -Force | Out-Null

    do {
        try {
            Send-Heartbeat
            $claim = Invoke-SignedPost '/internal/v1/assets/jobs/claim' ([ordered]@{
                worker_id = $WorkerId; agent_instance_id = $AgentInstanceId; load_1m = 0
                available_memory_mb = [int][Math]::Floor([double](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
            })
            if ($claim.job) { Execute-Bake $claim.job }
        }
        catch { Write-Error -ErrorAction Continue $_ }
        if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
    } while (-not $Once)
}
finally {
    if ($AgentMutexAcquired) {
        try { $AgentMutex.ReleaseMutex() } catch { Write-Warning $_ }
    }
    $AgentMutex.Dispose()
}
