[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^ssh-ed25519\s+[A-Za-z0-9+/=]+(?:\s+.*)?$')]
    [string]$AuthorizedKey,
    [string]$ControlAddress = '10.3.34.11'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this bootstrap from an elevated Windows PowerShell session'
}

$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
if ($capability.State -ne 'Installed') {
    $null = Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
}

$sshRoot = Join-Path $env:ProgramData 'ssh'
$keyFile = Join-Path $sshRoot 'administrators_authorized_keys'
New-Item -ItemType Directory -Path $sshRoot -Force | Out-Null
$existingKeys = if (Test-Path -LiteralPath $keyFile) {
    @(Get-Content -LiteralPath $keyFile | ForEach-Object { $_.Trim() } | Where-Object { $_ })
} else { @() }
if ($existingKeys -notcontains $AuthorizedKey.Trim()) {
    $AuthorizedKey.Trim() | Add-Content -LiteralPath $keyFile -Encoding Ascii
}

& "$env:SystemRoot\System32\icacls.exe" $keyFile /inheritance:r | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'failed to disable inherited ACLs on administrators_authorized_keys' }
& "$env:SystemRoot\System32\icacls.exe" $keyFile /grant:r `
    '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'failed to install restricted OpenSSH key ACLs' }

$ruleName = 'GPUControl-Windows-SSH-From-4090'
Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name $ruleName -DisplayName 'GPU Control Windows SSH from 4090' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalAddress '10.3.34.238' `
    -LocalPort 22 -RemoteAddress $ControlAddress -Profile Any | Out-Null
# Disable the capability's broad default rule only after the restricted rule is installed.
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | `
    Disable-NetFirewallRule

Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd
Restart-Service -Name sshd
Write-Output "WINDOWS_SSH_READY listen=10.3.34.238:22 remote=$ControlAddress auth=public-key"
