[CmdletBinding()]
param(
    [string]$ControlBaseUrl = 'https://lilithgames2',
    [string]$CaCertificate = 'C:\ProgramData\Li3D\MOFWorker\secrets\GPU_CONTROL_LAN_CA.crt',
    [string]$SecretFile = 'C:\ProgramData\Li3D\MOFWorker\secrets\asset_worker_hmac_secret.txt',
    [string]$InstallRoot = 'C:\ProgramData\Li3D\MOFWorker',
    [string]$BlenderExe = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe',
    [int]$PollSeconds = 2,
    [ValidateRange(10, 240)][int]$LeaseRenewalSeconds = 20,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$WorkerId = 'asset-worker-4070ti-mof-01'
$NodeId = 'worker-4070ti-animation-host-01'
$BlenderVersion = '5.2.0'
$SkillVersion = 'mof-windows-native-1.0.9-2026.08.19-v3'
$ScriptsRoot = Join-Path $InstallRoot 'scripts'
$JobsRoot = Join-Path $InstallRoot 'jobs'
$LogsRoot = Join-Path $InstallRoot 'logs'
$CurlExe = 'C:\Windows\System32\curl.exe'
$ControlResolve = 'lilithgames2:443:10.3.34.11'
$TaskKillExe = 'C:\Windows\System32\taskkill.exe'
$CurrentJobs = 0
$AgentInstanceId = [Guid]::NewGuid().ToString('N')
$AgentStartedAt = [DateTimeOffset]::UtcNow.ToString('o')
$AgentMutexName = "Global\GPUControl.MofAgent.$WorkerId"
$AgentMutex = New-Object System.Threading.Mutex($false, $AgentMutexName)
$AgentMutexAcquired = $false
$AgentLog = Join-Path $LogsRoot 'mof-agent.log'

$RuntimeFiles = [ordered]@{
    'mof_unwrap.py' = '70e98027f64b4389ec1f7086bb363e5d4a7a686b9472d17fa840ecb01dbd946d'
    'preflight_mof.py' = 'd4639ebd34128b02496599eef55c21ed1eab295c6117fc234c819003e491db40'
    'qa_uv.py' = 'a263d0fc05947d70988317972f9b0bb38e7c85a165274756d3c4dbf4e05f91c3'
    'blender_uv_fbx_units.py' = '67e98dc5db415a83736ee154856b2c3b54f057e69440d1edbc76e43873afa24e'
    'blender_uv_qa_adapter.py' = 'b33aa2f6fa00b71b371b793fb06adc0284b28c3f0b7cedf9f783ce402cf12464'
}

function Write-AgentLog([string]$Level, [string]$Message) {
    $line = '{0} {1} {2}' -f [DateTimeOffset]::UtcNow.ToString('o'), $Level, $Message
    $line | Add-Content -LiteralPath $AgentLog -Encoding UTF8
}

function Get-HexSha256Bytes([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-HmacSignature(
    [string]$Method,
    [string]$Path,
    [string]$Body,
    [string]$Timestamp,
    [string]$Nonce
) {
    $bodyBytes = [Text.Encoding]::UTF8.GetBytes($Body)
    $bodySha = Get-HexSha256Bytes $bodyBytes
    $message = "$($Method.ToUpperInvariant())`n$Path`n$Timestamp`n$Nonce`n$bodySha"
    $key = [Text.Encoding]::UTF8.GetBytes($script:WorkerSecret)
    $hmac = New-Object Security.Cryptography.HMACSHA256
    try {
        $hmac.Key = $key
        return ([BitConverter]::ToString(
            $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($message))
        )).Replace('-', '').ToLowerInvariant()
    }
    finally { $hmac.Dispose() }
}

function Invoke-SignedPost([string]$Path, [System.Collections.IDictionary]$Payload) {
    $body = $Payload | ConvertTo-Json -Depth 16 -Compress
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
    $nonce = [Guid]::NewGuid().ToString('N')
    $signature = Get-HmacSignature 'POST' $Path $body $timestamp $nonce
    $bodyFile = Join-Path $env:TEMP ("gpu-control-mof-body-" + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        [IO.File]::WriteAllText($bodyFile, $body, (New-Object Text.UTF8Encoding($false)))
        $response = & $CurlExe --ipv4 --resolve $ControlResolve `
            --silent --show-error --fail --cacert $CaCertificate --ssl-no-revoke `
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

function Invoke-LeasedJsonPost(
    [string]$Path,
    [string]$Lease,
    [System.Collections.IDictionary]$Payload
) {
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bodyFile = Join-Path $env:TEMP ("gpu-control-mof-lease-" + [Guid]::NewGuid().ToString('N') + '.json')
    try {
        [IO.File]::WriteAllText($bodyFile, $body, (New-Object Text.UTF8Encoding($false)))
        $response = & $CurlExe --ipv4 --resolve $ControlResolve `
            --silent --show-error --fail --cacert $CaCertificate --ssl-no-revoke `
            --connect-timeout 5 --max-time 20 `
            -X POST ($ControlBaseUrl + $Path) `
            -H 'Content-Type: application/json' `
            -H ("X-Asset-Lease: $Lease") `
            --data-binary ("@$bodyFile")
        if ($LASTEXITCODE -ne 0) { throw "leased POST failed: $Path ($LASTEXITCODE)" }
        return ($response | ConvertFrom-Json)
    }
    finally { Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue }
}

function Get-OptionalProperty($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Write-JsonUtf8NoBom([string]$Path, $Value) {
    $payload = $Value | ConvertTo-Json -Depth 32
    [IO.File]::WriteAllText($Path, $payload + "`n", (New-Object Text.UTF8Encoding($false)))
}

function Assert-FileHash([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "file missing: $Path" }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) { throw "SHA-256 mismatch: $Path=$actual" }
}

function ConvertTo-NativeProcessArgument([string]$Value) {
    if ($Value.IndexOf([char]0) -ge 0) { throw 'native process argument contains a NUL byte' }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
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

function Send-Heartbeat {
    $os = Get-CimInstance Win32_OperatingSystem
    $availableMb = [int][Math]::Floor([double]$os.FreePhysicalMemory / 1024)
    $payload = [ordered]@{
        worker_id = $WorkerId
        node_id = $NodeId
        display_name = '4070 Ti Native Windows MOF UV Worker'
        hostname = $env:COMPUTERNAME
        blender_version = $BlenderVersion
        skill_version = $SkillVersion
        cpu_count = [Environment]::ProcessorCount
        max_concurrency = 1
        current_jobs = $script:CurrentJobs
        load_1m = 0
        available_memory_mb = $availableMb
        agent_instance_id = $AgentInstanceId
        agent_started_at = $AgentStartedAt
    }
    $response = Invoke-SignedPost '/internal/v1/assets/workers/heartbeat' $payload
    if ([string](Get-OptionalProperty $response 'status') -ne 'ONLINE') {
        throw "MOF Worker heartbeat is not ONLINE: $($response | ConvertTo-Json -Compress)"
    }
}

function Invoke-LeaseRenewal(
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
                progress = [Math]::Min([Math]::Max($Progress, 0), 99)
                stage = $Stage
                message = $Message
                estimated_remaining_seconds = $EstimatedRemainingSeconds
            })
        }
        catch {
            $lastFailure = $_.Exception.Message
            if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
        }
    }
    throw "MOF lease renewal failed after 3 attempts: $lastFailure"
}

function Stop-NativeProcessTree($Process) {
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if ($Process.HasExited) { return }
    }
    catch { return }
    & $TaskKillExe /PID $Process.Id /T /F 2>&1 | Out-Null
    try {
        if (-not $Process.WaitForExit(15000)) {
            throw 'native process tree did not exit within 15 seconds'
        }
        $Process.Refresh()
        if (-not $Process.HasExited) { throw 'native process remained active after taskkill' }
    }
    catch { throw "MOF_PROCESS_TERMINATION_UNCONFIRMED: $($_.Exception.Message)" }
}

function Read-BoundedProcessOutput([string]$StdoutPath, [string]$StderrPath) {
    $limit = 16 * 1024 * 1024
    $size = 0
    foreach ($path in @($StdoutPath, $StderrPath)) {
        if (Test-Path -LiteralPath $path) { $size += (Get-Item -LiteralPath $path).Length }
    }
    if ($size -gt $limit) { throw 'Windows Blender output exceeded the 16 MiB safety limit' }
    $parts = @()
    foreach ($path in @($StdoutPath, $StderrPath)) {
        if (Test-Path -LiteralPath $path) { $parts += [IO.File]::ReadAllText($path) }
    }
    return ($parts -join "`n")
}

function Invoke-BlenderStage(
    [string[]]$Arguments,
    [string]$LogPath,
    [string]$JobId,
    [string]$Lease,
    [double]$Progress,
    [string]$Stage,
    [string]$Message,
    [int]$EstimatedRemainingSeconds,
    [string]$RequiredMarker = ''
) {
    $stdoutPath = Join-Path $env:TEMP ("gpu-control-mof-blender-stdout-" + [Guid]::NewGuid().ToString('N') + '.log')
    $stderrPath = Join-Path $env:TEMP ("gpu-control-mof-blender-stderr-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    $output = ''
    $exitCode = $null
    try {
        $argumentLine = (($Arguments | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $BlenderExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        while (-not $process.WaitForExit($LeaseRenewalSeconds * 1000)) {
            $renewal = Invoke-LeaseRenewal $JobId $Lease $Progress $Stage $Message $EstimatedRemainingSeconds
            if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                Stop-NativeProcessTree $process
                throw 'MOF job was cancelled while Windows Blender was running'
            }
            try { Send-Heartbeat }
            catch { Write-AgentLog 'WARN' "heartbeat failed during ${Stage}: $($_.Exception.Message)" }
        }
        $process.WaitForExit()
        $process.Refresh()
        $exitCode = $process.ExitCode
    }
    catch {
        Stop-NativeProcessTree $process
        throw
    }
    finally {
        try { $output = Read-BoundedProcessOutput $stdoutPath $stderrPath }
        finally {
            Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
            if ($null -ne $process) { $process.Dispose() }
        }
        if ($output) { $output | Add-Content -LiteralPath $LogPath -Encoding UTF8 }
    }
    if ($null -ne $exitCode -and [int]$exitCode -ne 0) {
        $tail = if ($output.Length -gt 8000) { $output.Substring($output.Length - 8000) } else { $output }
        throw "Windows Blender stage failed: stage=$Stage exit_code=$exitCode $tail"
    }
    if ($RequiredMarker -and $output -notmatch [Regex]::Escape($RequiredMarker)) {
        throw "Windows Blender stage completion marker missing: stage=$Stage marker=$RequiredMarker"
    }
}

function Invoke-StartupPreflight {
    if (-not (Test-Path -LiteralPath $BlenderExe -PathType Leaf)) { throw "Blender missing: $BlenderExe" }
    foreach ($entry in $RuntimeFiles.GetEnumerator()) {
        Assert-FileHash (Join-Path $ScriptsRoot ([string]$entry.Key)) ([string]$entry.Value)
    }
    $stdoutPath = Join-Path $env:TEMP ("gpu-control-mof-preflight-stdout-" + [Guid]::NewGuid().ToString('N') + '.log')
    $stderrPath = Join-Path $env:TEMP ("gpu-control-mof-preflight-stderr-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    $output = ''
    try {
        $args = @(
            '--background', '--disable-autoexec', '--python-exit-code', '1',
            '--python', (Join-Path $ScriptsRoot 'preflight_mof.py')
        )
        $argumentLine = (($args | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $BlenderExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        if (-not $process.WaitForExit(180000)) {
            Stop-NativeProcessTree $process
            throw 'UV_MOF_RUNTIME_UNAVAILABLE: Windows Blender MOF preflight timed out'
        }
        $process.WaitForExit()
        $process.Refresh()
        $output = Read-BoundedProcessOutput $stdoutPath $stderrPath
        $beginMarker = 'LI3D_MOF_PREFLIGHT_BEGIN'
        $endMarker = 'LI3D_MOF_PREFLIGHT_END'
        $beginIndex = $output.IndexOf($beginMarker, [StringComparison]::Ordinal)
        $endIndex = $output.IndexOf($endMarker, [StringComparison]::Ordinal)
        $preflightPayload = $null
        if ($beginIndex -ge 0 -and $endIndex -gt $beginIndex) {
            $jsonStart = $beginIndex + $beginMarker.Length
            $jsonText = $output.Substring($jsonStart, $endIndex - $jsonStart).Trim()
            try { $preflightPayload = $jsonText | ConvertFrom-Json }
            catch { $preflightPayload = $null }
        }
        $preflightAvailable = $null -ne $preflightPayload -and `
            [bool](Get-OptionalProperty $preflightPayload 'available')
        $preflightExitCode = $process.ExitCode
        if (
            ($null -ne $preflightExitCode -and [int]$preflightExitCode -ne 0) -or
            -not $preflightAvailable
        ) {
            throw (
                "UV_MOF_RUNTIME_UNAVAILABLE: Windows Blender MOF preflight failed: " +
                "exit_code=$preflightExitCode markers=$beginIndex/$endIndex output=$output"
            )
        }
        $versionOutput = & $BlenderExe --version 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch '^Blender 5\.2\.0') {
            throw "UV_MOF_RUNTIME_UNAVAILABLE: unexpected Blender version: $versionOutput"
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Invoke-LeasedDownload(
    [string]$Url,
    [string]$Destination,
    [string]$ExpectedSha256,
    [string]$JobId,
    [string]$Lease
) {
    $stderrPath = Join-Path $env:TEMP ("gpu-control-mof-download-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    try {
        $args = @(
            '--ipv4', '--resolve', $ControlResolve,
            '--silent', '--show-error', '--fail', '--cacert', $CaCertificate, '--ssl-no-revoke',
            '--connect-timeout', '5', '--max-time', '3600',
            '-H', "X-Asset-Lease: $Lease", '-o', $Destination, ($ControlBaseUrl + $Url)
        )
        $argumentLine = (($args | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $CurlExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardError $stderrPath
        while (-not $process.WaitForExit($LeaseRenewalSeconds * 1000)) {
            $renewal = Invoke-LeaseRenewal $JobId $Lease 2 'DOWNLOADING_INPUT' `
                'Native Windows MOF Worker is downloading and verifying the immutable input' 300
            if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                Stop-NativeProcessTree $process
                throw 'MOF job was cancelled while downloading input'
            }
            try { Send-Heartbeat } catch { Write-AgentLog 'WARN' $_.Exception.Message }
        }
        $process.WaitForExit()
        $process.Refresh()
        # Windows PowerShell 5 may expose a blank ExitCode after redirected
        # Start-Process execution.  The destination is unique per attempt, so
        # the immutable API-provided digest is the authoritative success gate.
        Assert-FileHash $Destination $ExpectedSha256
    }
    catch {
        Stop-NativeProcessTree $process
        throw
    }
    finally {
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Invoke-CompletionUpload(
    [string[]]$Arguments,
    [string]$JobId,
    [string]$Lease,
    [string]$LogPath
) {
    $stdoutPath = Join-Path $env:TEMP ("gpu-control-mof-upload-stdout-" + [Guid]::NewGuid().ToString('N') + '.json')
    $stderrPath = Join-Path $env:TEMP ("gpu-control-mof-upload-stderr-" + [Guid]::NewGuid().ToString('N') + '.log')
    $process = $null
    $responseText = ''
    try {
        $argumentLine = (($Arguments | ForEach-Object {
            ConvertTo-NativeProcessArgument ([string]$_)
        }) -join ' ')
        $process = Start-Process -FilePath $CurlExe -ArgumentList $argumentLine `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        while (-not $process.WaitForExit($LeaseRenewalSeconds * 1000)) {
            try {
                $renewal = Invoke-LeaseRenewal $JobId $Lease 97 'UPLOADING_ARTIFACTS' `
                    'Native Windows MOF Worker is uploading and validating five UV artifacts' 120
            }
            catch {
                if ($process.WaitForExit(2000)) { break }
                throw
            }
            if ([bool](Get-OptionalProperty $renewal 'cancel_requested')) {
                Stop-NativeProcessTree $process
                throw 'MOF result upload stopped because the job was cancelled'
            }
            try { Send-Heartbeat } catch { Write-AgentLog 'WARN' $_.Exception.Message }
        }
        $process.WaitForExit()
        $process.Refresh()
        $responseText = if (Test-Path -LiteralPath $stdoutPath) {
            [IO.File]::ReadAllText($stdoutPath)
        } else { '' }
        # The same PowerShell 5 ExitCode limitation applies here.  An upload
        # succeeds only when the API returns valid JSON with both durable
        # acceptance and the terminal SUCCEEDED state below.
        try { $response = $responseText | ConvertFrom-Json }
        catch { throw "result upload returned invalid JSON: $($_.Exception.Message)" }
        if ([string](Get-OptionalProperty $response 'status') -ne 'SUCCEEDED' -or `
            -not [bool](Get-OptionalProperty $response 'accepted')) {
            throw "result upload was not accepted: $responseText"
        }
    }
    catch {
        Stop-NativeProcessTree $process
        throw
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Execute-MofJob($Job) {
    $jobId = [string]$Job.job_id
    $lease = [string]$Job.lease_token
    if ($jobId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'invalid MOF job ID' }
    if ([string]$Job.job_type -ne 'UV_PROCESS_V2') { throw 'MOF Worker received unsupported job type' }
    if ([string]$Job.options.algorithm -ne 'mof_low_seam') { throw 'MOF Worker received non-MOF UV job' }
    $assetProfile = [string]$Job.options.asset_profile
    if ($assetProfile -notin @('complex_non_hardsurface', 'complex_multi_mesh')) {
        throw 'UV_MOF_ASSET_PROFILE_REQUIRED: MOF requires an approved complex asset profile'
    }
    $sourceFilename = [string]$Job.source_filename
    if ([IO.Path]::GetFileName($sourceFilename) -ne $sourceFilename) { throw 'unsafe source filename' }
    $extension = [IO.Path]::GetExtension($sourceFilename).ToLowerInvariant()
    if ($extension -notin @('.fbx', '.obj', '.glb', '.gltf', '.blend')) {
        throw "unsupported MOF input extension: $extension"
    }
    $generation = [Guid]::NewGuid().ToString('N')
    $jobRoot = Join-Path (Join-Path $JobsRoot $jobId) $generation
    $outputRoot = Join-Path $jobRoot 'output'
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    $inputPath = Join-Path $jobRoot $sourceFilename
    $stem = [IO.Path]::GetFileNameWithoutExtension($sourceFilename)
    $outputBlend = Join-Path $outputRoot "${stem}_PBR_UV.blend"
    $outputFbx = Join-Path $outputRoot "${stem}_PBR_UV.fbx"
    $outputReport = Join-Path $outputRoot "${stem}_PBR_UV_report.json"
    $blendQa = Join-Path $outputRoot "${stem}_PBR_UV_QA.json"
    $fbxQa = Join-Path $outputRoot "${stem}_PBR_UV_FBX_QA.json"
    $unitReport = Join-Path $outputRoot '.fbx-unit-contract.json'
    $jobLog = Join-Path $outputRoot 'mof-worker.log'
    $resolution = [int]$Job.options.resolution
    $padding = [int]$Job.options.padding_px
    if ($resolution -notin @(1024, 2048, 4096, 8192)) { throw 'invalid MOF resolution' }
    if ($padding -lt 2 -or $padding -gt 128) { throw 'invalid MOF padding' }

    try {
        $script:CurrentJobs = 1
        Send-Heartbeat
        $null = Invoke-LeaseRenewal $jobId $lease 2 'DOWNLOADING_INPUT' `
            'Native Windows MOF Worker is downloading and verifying the immutable input' 300
        Invoke-LeasedDownload ([string]$Job.input_url) $inputPath ([string]$Job.input_sha256) $jobId $lease

        Invoke-BlenderStage @(
            '--background', '--disable-autoexec', '--python-exit-code', '1',
            '--python', (Join-Path $ScriptsRoot 'mof_unwrap.py'), '--',
            '--input', $inputPath, '--output-blend', $outputBlend,
            '--output-fbx', $outputFbx, '--report', $outputReport,
            '--resolution', $resolution.ToString(), '--padding-px', $padding.ToString()
        ) $jobLog $jobId $lease 20 'UV_UNWRAPPING' `
            'Native Windows Blender is running MinistryOfFlat low-seam UV processing' 300 `
            'BLENDER_MOF_UV_COMPLETE'

        Invoke-BlenderStage @(
            '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
            '--python', (Join-Path $ScriptsRoot 'blender_uv_fbx_units.py'), '--',
            '--source-asset', $inputPath, '--input-blend', $outputBlend,
            '--output-fbx', $outputFbx, '--output-report', $unitReport
        ) $jobLog $jobId $lease 66 'UV_FBX_UNIT_PRESERVATION' `
            'Native Windows Blender is preserving and validating the source FBX unit contract' 90

        $unitPayload = Get-Content -LiteralPath $unitReport -Raw | ConvertFrom-Json
        if (-not [bool]$unitPayload.passed) { throw 'UV FBX source unit contract did not pass' }
        $reportPayload = Get-Content -LiteralPath $outputReport -Raw | ConvertFrom-Json
        $reportPayload | Add-Member -NotePropertyName algorithm -NotePropertyValue 'mof_low_seam' -Force
        $reportPayload | Add-Member -NotePropertyName asset_profile `
            -NotePropertyValue $assetProfile -Force
        $reportPayload | Add-Member -NotePropertyName input -NotePropertyValue $sourceFilename -Force
        $reportPayload | Add-Member -NotePropertyName fbx_unit_contract -NotePropertyValue $unitPayload -Force
        Write-JsonUtf8NoBom $outputReport $reportPayload

        foreach ($qaSpec in @(
            @('blend', $outputBlend, $blendQa, 'UV_QA_BLEND', 78),
            @('fbx_readback', $outputFbx, $fbxQa, 'UV_QA_FBX_READBACK', 90)
        )) {
            Invoke-BlenderStage @(
                '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                '--python', (Join-Path $ScriptsRoot 'blender_uv_qa_adapter.py'), '--',
                '--input', [string]$qaSpec[1], '--output', [string]$qaSpec[2]
            ) $jobLog $jobId $lease ([double]$qaSpec[4]) ([string]$qaSpec[3]) `
                ("Native Windows Blender is validating MOF UV: " + [string]$qaSpec[0]) 90 `
                'BLENDER_PBR_UV_QA_END'
            $qaPayload = Get-Content -LiteralPath ([string]$qaSpec[2]) -Raw | ConvertFrom-Json
            $qaPayload | Add-Member -NotePropertyName algorithm -NotePropertyValue 'mof_low_seam' -Force
            $qaPayload | Add-Member -NotePropertyName asset_profile `
                -NotePropertyValue $assetProfile -Force
            Write-JsonUtf8NoBom ([string]$qaSpec[2]) $qaPayload
            $hardFailures = @(Get-OptionalProperty $qaPayload 'hard_failures')
            if (-not [bool](Get-OptionalProperty $qaPayload 'passed') -or $hardFailures.Count -ne 0) {
                throw "UV_QA_FAILED: $($qaSpec[0]): $($hardFailures | ConvertTo-Json -Compress)"
            }
        }
        Remove-Item -LiteralPath $unitReport -Force

        $uploadArgs = @(
            '--ipv4', '--resolve', $ControlResolve,
            '--silent', '--show-error', '--fail-with-body', '--cacert', $CaCertificate,
            '--ssl-no-revoke', '--connect-timeout', '5', '--max-time', '3600',
            '-X', 'POST', ($ControlBaseUrl + "/internal/v1/assets/jobs/$jobId/uv-v2-complete"),
            '-H', "X-Asset-Lease: $lease",
            '-F', "blend=@$outputBlend;type=application/octet-stream",
            '-F', "fbx=@$outputFbx;type=application/octet-stream",
            '-F', "report=@$outputReport;type=application/json",
            '-F', "qa=@$blendQa;type=application/json",
            '-F', "fbx_qa=@$fbxQa;type=application/json"
        )
        Invoke-CompletionUpload $uploadArgs $jobId $lease $jobLog
        Remove-Item -LiteralPath $jobRoot -Recurse -Force
        Write-AgentLog 'INFO' "MOF job completed job_id=$jobId"
    }
    catch {
        $failureMessage = $_.Exception.Message
        $failureCode = if ($failureMessage -match 'UV_MOF_RUNTIME_UNAVAILABLE') {
            'UV_MOF_RUNTIME_UNAVAILABLE'
        }
        elseif ($failureMessage -match 'UV_MOF_ASSET_PROFILE_REQUIRED') {
            'UV_MOF_ASSET_PROFILE_REQUIRED'
        }
        elseif ($failureMessage -match 'UV_QA_FAILED|hard failures') {
            'UV_QA_FAILED'
        }
        else { 'BLENDER_EXECUTION_FAILED' }
        $retryable = $failureCode -eq 'BLENDER_EXECUTION_FAILED'
        try {
            $bounded = $failureMessage.Substring(0, [Math]::Min(3900, $failureMessage.Length))
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/fail" $lease ([ordered]@{
                code = $failureCode
                message = $bounded
                retryable = $retryable
            })
        }
        catch { Write-AgentLog 'ERROR' "failure report failed job_id=$jobId $($_.Exception.Message)" }
        Write-AgentLog 'ERROR' "MOF job failed job_id=$jobId code=$failureCode message=$failureMessage"
    }
    finally {
        $script:CurrentJobs = 0
        try { Send-Heartbeat } catch { Write-AgentLog 'WARN' $_.Exception.Message }
    }
}

try {
    New-Item -ItemType Directory -Path $JobsRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
    try {
        $AgentMutexAcquired = $AgentMutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] { $AgentMutexAcquired = $true }
    if (-not $AgentMutexAcquired) { throw "MOF_AGENT_INSTANCE_ALREADY_RUNNING: $WorkerId" }
    if (-not (Test-Path -LiteralPath $CurlExe)) { throw "curl.exe missing: $CurlExe" }
    if (-not (Test-Path -LiteralPath $TaskKillExe)) { throw "taskkill.exe missing: $TaskKillExe" }
    if (-not (Test-Path -LiteralPath $CaCertificate)) { throw "CA certificate missing: $CaCertificate" }
    if (-not (Test-Path -LiteralPath $SecretFile)) { throw "worker secret missing: $SecretFile" }
    $script:WorkerSecret = (Get-Content -LiteralPath $SecretFile -Raw).Trim()
    if ($script:WorkerSecret.Length -lt 32) { throw 'worker secret is too short' }
    Invoke-StartupPreflight
    Write-AgentLog 'INFO' (
        "native Windows MOF runtime verified worker_id=$WorkerId control_url=$ControlBaseUrl transport=ipv4-pinned"
    )

    do {
        try {
            Send-Heartbeat
            $claim = Invoke-SignedPost '/internal/v1/assets/jobs/claim' ([ordered]@{
                worker_id = $WorkerId
                node_id = $NodeId
                agent_instance_id = $AgentInstanceId
                load_1m = 0
                available_memory_mb = [int][Math]::Floor(
                    [double](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024
                )
                accepts_codex_jobs = $false
                uv_algorithms = @('mof_low_seam')
            })
            if ($null -ne (Get-OptionalProperty $claim 'job')) { Execute-MofJob $claim.job }
        }
        catch {
            Write-AgentLog 'ERROR' $_.Exception.Message
            Write-Error -ErrorAction Continue $_
        }
        if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
    } while (-not $Once)
}
catch {
    try {
        New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
        Write-AgentLog 'FATAL' $_.Exception.ToString()
    }
    catch { Write-Error -ErrorAction Continue $_ }
    throw
}
finally {
    if ($AgentMutexAcquired) {
        try { $AgentMutex.ReleaseMutex() } catch { Write-Warning $_ }
    }
    $AgentMutex.Dispose()
}
