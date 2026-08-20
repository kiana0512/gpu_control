$ErrorActionPreference = 'Stop'

$Distro = 'Ubuntu'
$ListenAddress = '10.3.34.238'
$ControlAddress = '10.3.34.11'
$WslExe = "$env:SystemRoot\System32\wsl.exe"
$LogDirectory = 'C:\ProgramData\GPUControl\logs'
$LogPath = Join-Path $LogDirectory 'wsl-ssh-proxy.log'
$Mappings = @(
  [pscustomobject]@{ Name = 'SSH'; ListenPort = 2222; ConnectPort = 22 },
  [pscustomobject]@{ Name = 'ComfyUI'; ListenPort = 8188; ConnectPort = 8188 },
  [pscustomobject]@{ Name = 'NodeExporter'; ListenPort = 9100; ConnectPort = 9100 },
  [pscustomobject]@{ Name = 'NodeAgent'; ListenPort = 9201; ConnectPort = 9201 }
)

function Write-ChangeLog([string]$Message) {
  New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
  $line = '{0} {1}' -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Message
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Test-FirewallRule(
  [string]$RuleName,
  [string[]]$ExpectedPorts
) {
  $Rule = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
  if (-not $Rule) {
    return $false
  }
  $AddressFilter = $Rule | Get-NetFirewallAddressFilter
  $PortFilter = $Rule | Get-NetFirewallPortFilter
  $ActualPorts = @($PortFilter.LocalPort | ForEach-Object { [string]$_ })
  $MissingPorts = @($ExpectedPorts | Where-Object { $ActualPorts -notcontains [string]$_ })
  return (
    $Rule.Enabled -eq 'True' -and
    $Rule.Direction -eq 'Inbound' -and
    $Rule.Action -eq 'Allow' -and
    $AddressFilter.LocalAddress -contains $ListenAddress -and
    $AddressFilter.RemoteAddress -contains $ControlAddress -and
    $PortFilter.Protocol -eq 'TCP' -and
    $MissingPorts.Count -eq 0
  )
}

& $WslExe -d $Distro -u root -- systemctl start ssh
if ($LASTEXITCODE -ne 0) {
  throw "Unable to start ssh in WSL distribution: $Distro"
}

$AddressJson = & $WslExe -d $Distro -u root -- ip -j -4 addr show dev eth0
$InterfaceInfo = $AddressJson | ConvertFrom-Json
$WslIp = @(
  $InterfaceInfo.addr_info |
    Where-Object { $_.family -eq 'inet' } |
    Select-Object -ExpandProperty local -First 1
)[0]

if (-not $WslIp) {
  throw 'Unable to discover WSL2 IPv4 address'
}

$ParsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($WslIp, [ref]$ParsedAddress)) {
  throw "Invalid WSL IPv4 address: $WslIp"
}
if ($WslIp -eq $ListenAddress -or $WslIp.StartsWith('127.')) {
  throw "Unsafe WSL target address: $WslIp"
}

Set-Service iphlpsvc -StartupType Automatic
Start-Service iphlpsvc

$ProxyChanged = $false
$ExistingProxyLines = & netsh interface portproxy show v4tov4
foreach ($Mapping in $Mappings) {
  $ExpectedMappingExists = $false
  foreach ($Line in $ExistingProxyLines) {
    $Fields = @($Line.Trim() -split '\s+' | Where-Object { $_ })
    if (
      $Fields.Count -eq 4 -and
      $Fields[0] -eq $ListenAddress -and
      $Fields[1] -eq [string]$Mapping.ListenPort -and
      $Fields[2] -eq $WslIp -and
      $Fields[3] -eq [string]$Mapping.ConnectPort
    ) {
      $ExpectedMappingExists = $true
      break
    }
  }

  if (-not $ExpectedMappingExists) {
    & netsh interface portproxy delete v4tov4 `
      listenaddress=$ListenAddress listenport=$($Mapping.ListenPort) | Out-Null
    & netsh interface portproxy add v4tov4 `
      listenaddress=$ListenAddress listenport=$($Mapping.ListenPort) `
      connectaddress=$WslIp connectport=$($Mapping.ConnectPort) protocol=tcp
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to create Windows portproxy rule for $($Mapping.Name)"
    }
    $ProxyChanged = $true
    Write-ChangeLog (
      "portproxy_updated service=$($Mapping.Name) " +
      "listen=${ListenAddress}:$($Mapping.ListenPort) " +
      "target=${WslIp}:$($Mapping.ConnectPort)"
    )
  }
}

$SshFirewallName = 'GPUControl-4070-SSH-From-4090'
$SshFirewallDisplayName = 'GPU Control SSH 2222 from 4090'
$SshFirewallChanged = -not (Test-FirewallRule $SshFirewallName @('2222'))
if ($SshFirewallChanged) {
  Get-NetFirewallRule -Name $SshFirewallName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
  New-NetFirewallRule `
    -Name $SshFirewallName `
    -DisplayName $SshFirewallDisplayName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $ListenAddress `
    -LocalPort 2222 `
    -RemoteAddress $ControlAddress `
    -Profile Any | Out-Null
  Write-ChangeLog "firewall_updated service=SSH local=${ListenAddress}:2222 remote=${ControlAddress}"
}

$RuntimeFirewallName = 'GPUControl-4070-Runtime-From-4090'
$RuntimeFirewallDisplayName = 'GPU Control Runtime 8188 9100 9201 from 4090'
$RuntimeFirewallChanged = -not (
  Test-FirewallRule $RuntimeFirewallName @('8188', '9100', '9201')
)
if ($RuntimeFirewallChanged) {
  Get-NetFirewallRule -Name $RuntimeFirewallName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
  Get-NetFirewallRule -DisplayName $RuntimeFirewallDisplayName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
  New-NetFirewallRule `
    -Name $RuntimeFirewallName `
    -DisplayName $RuntimeFirewallDisplayName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $ListenAddress `
    -LocalPort 8188,9100,9201 `
    -RemoteAddress $ControlAddress `
    -Profile Any | Out-Null
  Write-ChangeLog (
    "firewall_updated service=Runtime local=${ListenAddress}:8188,9100,9201 " +
    "remote=${ControlAddress}"
  )
}

Write-Output "WSL_IPV4=$WslIp"
Write-Output "PORTPROXY_CHANGED=$ProxyChanged"
Write-Output "FIREWALL_CHANGED=$($SshFirewallChanged -or $RuntimeFirewallChanged)"
