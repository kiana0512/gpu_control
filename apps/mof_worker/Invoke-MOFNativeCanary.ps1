[CmdletBinding()]
param(
    [string]$StagingRoot = 'C:\ProgramData\Li3D\MOFWorker\staging\native-windows-v1-20260818',
    [string]$OutputRoot = 'C:\ProgramData\Li3D\MOFWorker\acceptance\native-v2-organic-20260818',
    [string]$BlenderExe = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

foreach ($required in @(
    $BlenderExe,
    (Join-Path $StagingRoot 'create_canary.py'),
    (Join-Path $StagingRoot 'mof_unwrap.py'),
    (Join-Path $StagingRoot 'blender_uv_fbx_units.py'),
    (Join-Path $StagingRoot 'blender_uv_qa_adapter.py'),
    (Join-Path $StagingRoot 'qa_uv.py')
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "required canary dependency missing: $required"
    }
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw "acceptance output already exists: $OutputRoot"
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$inputFbx = Join-Path $OutputRoot 'mof_complex_nonhardsurface.fbx'
$outputBlend = Join-Path $OutputRoot 'mof_complex_nonhardsurface_PBR_UV.blend'
$outputFbx = Join-Path $OutputRoot 'mof_complex_nonhardsurface_PBR_UV.fbx'
$outputReport = Join-Path $OutputRoot 'mof_complex_nonhardsurface_PBR_UV_report.json'
$unitReport = Join-Path $OutputRoot 'fbx_unit_contract.json'
$blendQa = Join-Path $OutputRoot 'mof_complex_nonhardsurface_PBR_UV_QA.json'
$fbxQa = Join-Path $OutputRoot 'mof_complex_nonhardsurface_PBR_UV_FBX_QA.json'
$acceptanceLog = Join-Path $OutputRoot 'acceptance.log'

function Invoke-BlenderStage {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $stageOutput = @(& $BlenderExe @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $stageOutput | Add-Content -LiteralPath $acceptanceLog -Encoding UTF8
    Write-Output "STAGE=$Stage EXIT=$exitCode"
    if ($exitCode -ne 0) {
        $stageOutput | Select-Object -Last 120 | Write-Output
        throw "Blender canary stage failed: $Stage exit=$exitCode"
    }
}

Invoke-BlenderStage 'CREATE_ORGANIC' @(
    '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
    '--python', (Join-Path $StagingRoot 'create_canary.py'), '--', $inputFbx
)

# MOF must retain the installed add-on and therefore intentionally omits
# --factory-startup. Every other Blender stage is isolated with factory startup.
Invoke-BlenderStage 'MOF_UNWRAP' @(
    '--background', '--disable-autoexec', '--python-exit-code', '1',
    '--python', (Join-Path $StagingRoot 'mof_unwrap.py'), '--',
    '--input', $inputFbx,
    '--output-blend', $outputBlend,
    '--output-fbx', $outputFbx,
    '--report', $outputReport,
    '--resolution', '2048',
    '--padding-px', '10'
)

Invoke-BlenderStage 'FBX_UNITS' @(
    '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
    '--python', (Join-Path $StagingRoot 'blender_uv_fbx_units.py'), '--',
    '--source-asset', $inputFbx,
    '--input-blend', $outputBlend,
    '--output-fbx', $outputFbx,
    '--output-report', $unitReport
)

Invoke-BlenderStage 'QA_BLEND' @(
    '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
    '--python', (Join-Path $StagingRoot 'blender_uv_qa_adapter.py'), '--',
    '--input', $outputBlend,
    '--output', $blendQa
)

Invoke-BlenderStage 'QA_FBX' @(
    '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
    '--python', (Join-Path $StagingRoot 'blender_uv_qa_adapter.py'), '--',
    '--input', $outputFbx,
    '--output', $fbxQa
)

$reportPayload = Get-Content -LiteralPath $outputReport -Raw | ConvertFrom-Json
$unitPayload = Get-Content -LiteralPath $unitReport -Raw | ConvertFrom-Json
$blendQaPayload = Get-Content -LiteralPath $blendQa -Raw | ConvertFrom-Json
$fbxQaPayload = Get-Content -LiteralPath $fbxQa -Raw | ConvertFrom-Json
$blendHardFailures = @($blendQaPayload.hard_failures)
$fbxHardFailures = @($fbxQaPayload.hard_failures)
if (
    -not [bool]$unitPayload.passed -or
    -not [bool]$blendQaPayload.passed -or
    -not [bool]$fbxQaPayload.passed -or
    $blendHardFailures.Count -ne 0 -or
    $fbxHardFailures.Count -ne 0
) {
    throw 'native MOF acceptance JSON gate failed'
}

$files = Get-ChildItem -LiteralPath $OutputRoot -File | Sort-Object Name | ForEach-Object {
    [pscustomobject]@{
        name = $_.Name
        size = $_.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    }
}

[pscustomobject]@{
    acceptance = 'PASS'
    output = $OutputRoot
    report = [pscustomobject]@{
        mesh_object_count = $reportPayload.mesh_object_count
        loose_parts_processed = $reportPayload.loose_parts_processed
        face_parts_processed_by_mof = $reportPayload.face_parts_processed_by_mof
        materials = $reportPayload.materials
        uv_layers = $reportPayload.uv_layers
        uv_audit = $reportPayload.uv_audit
    }
    unit_contract = $unitPayload
    blend_qa = [pscustomobject]@{
        passed = $blendQaPayload.passed
        hard_failures = $blendHardFailures
        reports = $blendQaPayload.reports
    }
    fbx_qa = [pscustomobject]@{
        passed = $fbxQaPayload.passed
        hard_failures = $fbxHardFailures
        reports = $fbxQaPayload.reports
    }
    files = $files
} | ConvertTo-Json -Depth 12
