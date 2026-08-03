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

function Send-Heartbeat {
    $os = Get-CimInstance Win32_OperatingSystem
    $availableMb = [int][Math]::Floor([double]$os.FreePhysicalMemory / 1024)
    $payload = [ordered]@{
        worker_id = $WorkerId; node_id = $NodeId
        display_name = ('3090-B Windows Substance Baker #{0:D2}' -f $InstanceId); hostname = $env:COMPUTERNAME
        blender_version = 'substance-15.1.0'; skill_version = 'substance-baker-2026.08.03-v3'
        cpu_count = [Environment]::ProcessorCount; max_concurrency = 1
        current_jobs = $script:CurrentJobs; load_1m = 0; available_memory_mb = $availableMb
    }
    $null = Invoke-SignedPost '/internal/v1/assets/workers/heartbeat' $payload
}

function Assert-ComfyUiProcessStable([string]$ExpectedIdentity = '') {
    # The control plane has already drained 3090-B and waited for its current
    # prompt to finish before a Baker can claim.  Keep the idle ComfyUI process
    # alive, request no model eviction, and preserve the opportunity to reuse
    # its hot cache. Actual VRAM residency is verified by the next real job.
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

function Enter-BakerGpuFence([string]$JobId) {
    # The durable, multi-worker fence is owned by Asset API in PostgreSQL.
    # Keep only this attempt's ComfyUI identity locally so a dead process or
    # host reboot cannot leave a stale shared file that poisons later claims.
    return Assert-ComfyUiProcessStable
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

function Stop-BakerProcess($Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            $Process.Kill()
            $null = $Process.WaitForExit(10000)
        }
    }
    catch { Write-Warning "failed to stop Substance Baker process: $($_.Exception.Message)" }
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
        $exitCode = $process.ExitCode
    }
    catch {
        Stop-BakerProcess $process
        throw
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
    if ($exitCode -ne 0) { throw "Substance Baker exited with code $exitCode" }
    if (-not (($lines -join "`n") -match 'Bake finished successfully')) {
        throw 'Substance Baker success marker missing'
    }
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
            progress = 5; stage = 'GPU_FENCING'; message = '3090-B inference drained; ComfyUI stays running and no model eviction is requested while native Windows Baker runs'; estimated_remaining_seconds = 540
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
            Invoke-BakerCommand $args $logPath $jobId $lease 20 'BAKING_AO' `
                'SAL + SoRa is generating ambient occlusion' 420
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
            Invoke-BakerCommand $args $logPath $jobId $lease 55 'BAKING_NORMAL' `
                'SAL + SoRa is generating a DirectX tangent-space normal map' 300
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
                Invoke-BakerCommand $args $logPath $jobId $lease 15 'BAKING_TEXTURE_TRANSFER' `
                    'Projecting Base Color, Roughness, and Metallic' 480
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
                Invoke-BakerCommand $args $logPath $jobId $lease 40 'BAKING_GEOMETRY_MAPS' `
                    'Generating AO, Curvature, Thickness, and Position maps' 330
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
                Invoke-BakerCommand $args $logPath $jobId $lease 72 'BAKING_NORMALS' `
                    'Generating DirectX, OpenGL, and world-space normal maps' 180
            }
        }

        $hashes = [ordered]@{}
        foreach ($outputRole in @('base_color', 'roughness', 'metallic', 'ao', 'normal_dx', 'normal_gl', 'world_normal', 'curvature', 'thickness', 'position')) {
            $outputFile = Join-Path $outputRoot "asset_$outputRole.png"
            if (Test-Path $outputFile) {
                $hashes[$outputRole] = (Get-FileHash $outputFile -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }

        Exit-BakerGpuFence $jobId $comfyProcessIdentity
        $fenceEntered = $false
        $comfyProcessContinuityVerified = $true
        $resultPath = Join-Path $outputRoot 'baker_result.json'
        $resultJson = [ordered]@{
            schema_version = 1; job_id = $jobId; status = 'SUCCEEDED'; profile = $profile
            tool = [ordered]@{ version = '15.1.0'; exe_sha256 = $ExpectedBakerSha256 }
            execution = [ordered]@{
                exit_code = 0; gpu_backends = @('SAL', 'SoRa')
                gpu_uuid = 'GPU-092a5184-5857-d196-5df2-efa9503368aa'
                comfyui_cache_policy = 'no_explicit_eviction_process_preserved'
                comfyui_container_restarted = $false
                comfyui_process_continuity_verified = $comfyProcessContinuityVerified
            }
            output_sha256 = $hashes
        } | ConvertTo-Json -Depth 8
        # Windows PowerShell 5.1 Set-Content -Encoding UTF8 writes a BOM.  The
        # control plane validates strict UTF-8 JSON, so write explicitly BOMless.
        [IO.File]::WriteAllText($resultPath, $resultJson, (New-Object Text.UTF8Encoding($false)))

        $curlArgs = @('--silent', '--show-error', '--fail', '--cacert', $CaCertificate, '--ssl-no-revoke',
            '-X', 'POST', ($ControlBaseUrl + "/internal/v1/assets/jobs/$jobId/substance-complete"),
            '-H', "X-Asset-Lease: $lease", '-F', "result=@$resultPath;type=application/json",
            '-F', "log=@$logPath;type=text/plain")
        foreach ($outputRole in $hashes.Keys) {
            $curlArgs += @('-F', "$outputRole=@$(Join-Path $outputRoot "asset_$outputRole.png");type=image/png")
        }
        $response = & $CurlExe @curlArgs
        if ($LASTEXITCODE -ne 0) { throw "result upload failed: $response" }
    }
    catch {
        $failureMessage = $_.Exception.Message
        $cacheContinuityFailure = $failureMessage.StartsWith($ComfyContinuityError)
        if ($fenceEntered) {
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
        try {
            $failureCode = if ($cacheContinuityFailure) { $ComfyContinuityError } else { 'SUBSTANCE_EXECUTION_FAILED' }
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/fail" $lease ([ordered]@{
                code = $failureCode
                message = $failureMessage.Substring(0, [Math]::Min(3900, $failureMessage.Length))
                retryable = -not $cacheContinuityFailure
            })
        } catch { Write-Error $_ }
        throw $failureMessage
    }
    finally {
        $script:CurrentJobs = 0
        try { Send-Heartbeat } catch { Write-Warning $_ }
    }
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
            worker_id = $WorkerId; load_1m = 0
            available_memory_mb = [int][Math]::Floor([double](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
        })
        if ($claim.job) { Execute-Bake $claim.job }
    }
    catch { Write-Error -ErrorAction Continue $_ }
    if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
} while (-not $Once)
