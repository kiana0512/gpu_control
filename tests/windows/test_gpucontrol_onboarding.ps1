# Read-only regression checks: parse source and execute extracted pure/mocked
# fragments. Never call WSL, firewall, ACL, service, or task mutation commands.
param([string]$RepositoryRoot = (Join-Path $PSScriptRoot '..\..'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$asts = @{}
foreach ($name in @('Initialize-GPUControlWindowsNode.ps1', 'Update-GPUControlWslProxy.ps1')) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $RepositoryRoot "scripts/$name"), [ref]$tokens, [ref]$errors
    )
    if ($errors.Count) { throw "Parse failure: $name" }
    $asts[$name] = $ast
}
$init = $asts['Initialize-GPUControlWindowsNode.ps1']
$getDistros = $init.Find({param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-Distros'
}, $true)
Invoke-Expression $getDistros.Extent.Text
$WslExe = 'Invoke-WslFixture'
$script:fixtureExists = $false
$script:fixtureError = $false
$script:fixtureExit = 0
$script:fixtureOutput = @()
function Test-Path { param([string]$LiteralPath) return $script:fixtureExists }
function Invoke-WslFixture {
    if ($script:fixtureError) { Write-Error 'Fixture: WSL platform is absent' }
    $global:LASTEXITCODE = $script:fixtureExit
    $script:fixtureOutput
}
try {
    if (@(Get-Distros).Count -ne 0) { throw 'Missing executable must return empty inventory' }
    $script:fixtureExists = $true; $script:fixtureError = $true
    if (@(Get-Distros).Count -ne 0) { throw 'Native stderr must not abort inventory' }
    $script:fixtureError = $false; $script:fixtureExit = 1
    if (@(Get-Distros).Count -ne 0) { throw 'Failed native query must return empty inventory' }
    $script:fixtureExit = 0; $script:fixtureOutput = @("Ubuntu-22.04`0", ' ', 'Debian')
    if ((@(Get-Distros) -join ',') -ne 'Ubuntu-22.04,Debian') { throw 'Distro normalization failed' }
} finally { Remove-Item Function:\Test-Path; Remove-Item Function:\Invoke-WslFixture }

$proxy = $asts['Update-GPUControlWslProxy.ps1']
$ruleMatch = $proxy.Find({param($n)
    $n -is [Management.Automation.Language.AssignmentStatementAst] -and
    $n.Left.Extent.Text -eq '$RuleMatches' -and $n.Right.Extent.Text -match 'AddressFilter'
}, $true)
if (-not $ruleMatch) { throw 'Missing firewall idempotence expression' }
$Rule = [pscustomobject]@{Enabled='True';Direction='Inbound';Action='Allow'}
$Config = [pscustomobject]@{WindowsIP='10.3.34.18';ControlAddress='10.3.34.11'}
$Ports = @('2222')
$PortFilter = [pscustomobject]@{Protocol='TCP';LocalPort='2222'}
$AddressFilter = [pscustomobject]@{LocalAddress='10.3.34.18';RemoteAddress='10.3.34.11'}
Invoke-Expression $ruleMatch.Extent.Text
if (-not $RuleMatches) { throw 'Scalar address should preserve the matching rule' }
$AddressFilter.LocalAddress = @('10.3.34.18'); $AddressFilter.RemoteAddress = @('10.3.34.11')
Invoke-Expression $ruleMatch.Extent.Text
if (-not $RuleMatches) { throw 'Single-element array should preserve the matching rule' }
$AddressFilter.RemoteAddress = @('10.3.34.11', '10.3.34.12')
Invoke-Expression $ruleMatch.Extent.Text
if ($RuleMatches) { throw 'Extra remote source must fail verification' }
$AddressFilter.RemoteAddress = '10.3.34.12'
Invoke-Expression $ruleMatch.Extent.Text
if ($RuleMatches) { throw 'Different remote source must fail verification' }
$AddressFilter.RemoteAddress = '10.3.34.11'; $PortFilter.LocalPort = '8188'
Invoke-Expression $ruleMatch.Extent.Text
if ($RuleMatches) { throw 'Different local port must fail verification' }
Write-Output 'ONBOARDING_REGRESSION_PASS parse missing_wsl native_stderr failed_query distro_normalization scalar_address array_address remote_source port_filter'
