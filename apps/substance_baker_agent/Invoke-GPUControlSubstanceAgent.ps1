[CmdletBinding()]
param(
    [string]$ControlBaseUrl = 'https://10.3.34.11',
    [string]$CaCertificate = 'D:\GPUControl\secrets\GPU_CONTROL_LAN_CA.crt',
    [string]$SecretFile = 'D:\GPUControl\secrets\asset_worker_hmac_secret.txt',
    [string]$JobsRoot = 'D:\GPUControl\jobs',
    [string]$BakerExe = 'C:\Program Files\Adobe\Adobe Substance 3D Designer\substance3d_baker.exe',
    [string]$WslDistribution = 'Ubuntu-22.04',
    [string]$WslComfyContainer = 'gpu-control-node-comfyui-1',
    [string]$FenceRoot = 'D:\GPUControl\state',
    [ValidateRange(1, 16)][int]$InstanceId = 1,
    [ValidateRange(1, 16)][int]$InstanceCount = 4,
    [int]$PollSeconds = 2,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$WorkerId = 'asset-worker-3090-b-windows-{0:D2}' -f $InstanceId
$NodeId = 'worker-3090-b'
$ExpectedBakerSha256 = '7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D'
$WslExe = 'C:\Windows\System32\wsl.exe'
$CurlExe = 'C:\Windows\System32\curl.exe'
$CurrentJobs = 0
$FenceStatePath = Join-Path $FenceRoot 'substance_baker_fence.json'
$FenceMutexName = 'Global\GPUControl-Substance-Baker-Fence-v2'

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
        blender_version = 'substance-15.1.0'; skill_version = 'substance-baker-2026.07.29-v2'
        cpu_count = [Environment]::ProcessorCount; max_concurrency = 1
        current_jobs = $script:CurrentJobs; load_1m = 0; available_memory_mb = $availableMb
    }
    $null = Invoke-SignedPost '/internal/v1/assets/workers/heartbeat' $payload
}

function Set-ComfyUiRunning([bool]$Running) {
    $verb = if ($Running) { 'start' } else { 'stop' }
    $arguments = @('-d', $WslDistribution, '-u', 'gpucontrol', '--', 'docker', $verb)
    if (-not $Running) { $arguments += @('--time', '60') }
    $arguments += $WslComfyContainer
    $output = & $WslExe @arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "WSL docker $verb failed: $output" }
    if ($Running) {
        $deadline = (Get-Date).AddMinutes(3)
        do {
            Start-Sleep -Seconds 3
            $state = & $WslExe -d $WslDistribution -u gpucontrol -- docker inspect `
                --format '{{.State.Health.Status}}' $WslComfyContainer 2>$null
            if ($state -eq 'healthy') { return }
        } while ((Get-Date) -lt $deadline)
        throw 'ComfyUI did not become healthy after native Baker execution'
    }
}

function Read-BakerFenceJobs {
    if (-not (Test-Path -LiteralPath $FenceStatePath)) { return @() }
    try {
        $payload = Get-Content -LiteralPath $FenceStatePath -Raw | ConvertFrom-Json
        return @($payload.active_job_ids | ForEach-Object { [string]$_ } | Where-Object { $_ })
    }
    catch {
        throw "invalid shared Baker fence state: $FenceStatePath"
    }
}

function Write-BakerFenceJobs([string[]]$JobIds) {
    New-Item -ItemType Directory -Path $FenceRoot -Force | Out-Null
    $temporary = "$FenceStatePath.$([Guid]::NewGuid().ToString('N')).tmp"
    $payload = [ordered]@{
        schema_version = 2
        active_job_ids = @($JobIds)
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($temporary, $payload, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $FenceStatePath -Force
}

function Enter-BakerGpuFence([string]$JobId) {
    $mutex = New-Object Threading.Mutex($false, $FenceMutexName)
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne([TimeSpan]::FromMinutes(5))
        if (-not $acquired) { throw 'timed out acquiring shared Baker GPU fence' }
        $active = @(Read-BakerFenceJobs)
        if ($active.Count -eq 0) { Set-ComfyUiRunning $false }
        if ($active -notcontains $JobId) { $active += $JobId }
        Write-BakerFenceJobs $active
    }
    finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Exit-BakerGpuFence([string]$JobId) {
    $mutex = New-Object Threading.Mutex($false, $FenceMutexName)
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne([TimeSpan]::FromMinutes(5))
        if (-not $acquired) { throw 'timed out releasing shared Baker GPU fence' }
        $active = @(Read-BakerFenceJobs | Where-Object { $_ -ne $JobId })
        Write-BakerFenceJobs $active
        if ($active.Count -eq 0) { Set-ComfyUiRunning $true }
    }
    finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
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

function Invoke-BakerCommand([string[]]$Arguments, [string]$LogPath) {
    # Substance writes ordinary INFO records to stderr.  With the agent-wide
    # ErrorActionPreference=Stop, PowerShell 5.1 otherwise promotes the first
    # INFO line to a terminating NativeCommandError and kills a healthy bake.
    # Continue only for the native invocation, then decide success from its
    # exit code and the Baker success marker below.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = & $BakerExe @Arguments 2>&1 | ForEach-Object { $_.ToString() }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $lines | Add-Content -LiteralPath $LogPath -Encoding UTF8
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
            progress = 5; stage = 'GPU_FENCING'; message = '3090-B WSL inference fenced; switching to native Windows Baker'; estimated_remaining_seconds = 540
        })
        Enter-BakerGpuFence $jobId
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
            Invoke-BakerCommand $args $logPath
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
            Invoke-BakerCommand $args $logPath
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
                Invoke-BakerCommand $args $logPath
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
                Invoke-BakerCommand $args $logPath
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
                Invoke-BakerCommand $args $logPath
            }
        }

        $hashes = [ordered]@{}
        foreach ($outputRole in @('base_color', 'roughness', 'metallic', 'ao', 'normal_dx', 'normal_gl', 'world_normal', 'curvature', 'thickness', 'position')) {
            $outputFile = Join-Path $outputRoot "asset_$outputRole.png"
            if (Test-Path $outputFile) {
                $hashes[$outputRole] = (Get-FileHash $outputFile -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }

        Exit-BakerGpuFence $jobId
        $fenceEntered = $false
        $resultPath = Join-Path $outputRoot 'baker_result.json'
        $resultJson = [ordered]@{
            schema_version = 1; job_id = $jobId; status = 'SUCCEEDED'; profile = $profile
            tool = [ordered]@{ version = '15.1.0'; exe_sha256 = $ExpectedBakerSha256 }
            execution = [ordered]@{ exit_code = 0; gpu_backends = @('SAL', 'SoRa'); gpu_uuid = 'GPU-092a5184-5857-d196-5df2-efa9503368aa' }
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
        if ($fenceEntered) {
            try { Exit-BakerGpuFence $jobId; $fenceEntered = $false } catch { Write-Error $_ }
        }
        try {
            $null = Invoke-LeasedJsonPost "/internal/v1/assets/jobs/$jobId/fail" $lease ([ordered]@{
                code = 'SUBSTANCE_EXECUTION_FAILED'; message = $_.Exception.Message.Substring(0, [Math]::Min(3900, $_.Exception.Message.Length)); retryable = $true
            })
        } catch { Write-Error $_ }
        throw
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
New-Item -ItemType Directory -Path $FenceRoot -Force | Out-Null

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
