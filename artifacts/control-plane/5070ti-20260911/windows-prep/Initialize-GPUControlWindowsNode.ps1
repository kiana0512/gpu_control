#requires -Version 5.1
<#
GPU Control Windows / WSL2 onboarding, including RTX 5070 Ti hosts.
Run Inspect first, then apply one named stage at a time in elevated Windows
PowerShell as the Windows user who owns (or will own) the WSL distribution.
Never resets a distro, reboots Windows, terminates WSL, or installs GPU workloads.
#>
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'WindowsSsh', 'WslPlatform', 'WslSsh', 'Persistence')]
    [string]$Stage = 'Inspect',
    [switch]$Apply,
    [string]$WindowsIP,
    [string]$WindowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [string]$Distro,
    [ValidatePattern('^[a-z_][a-z0-9_-]{0,31}$')][string]$WslUser,
    [string]$PublicKeyPath,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')][string]$NodeName,
    [string]$ControlAddress = '10.3.34.11',
    [ValidateRange(1, 65535)][int[]]$RuntimePorts = @(),
    [switch]$InstallDistro,
    [switch]$EnableWslSudo
)

# Local adaptation: accept overlapping allow rules only while an exact,
# all-profile block rule independently rejects every source except the controller.
function Test-GpuControlSourceBlock([string]$Control, [int[]]$ManagedPorts) {
    if ($Control -ne '10.3.34.11') { return $false }
    $b = Get-NetFirewallRule -PolicyStore ActiveStore -Name 'GPUControl-worker-5070ti-01-Management-BlockOtherSources' -ErrorAction SilentlyContinue
    if (-not $b -or [string]$b.Enabled -ne 'True' -or [string]$b.Direction -ne 'Inbound' -or [string]$b.Action -ne 'Block' -or [string]$b.Profile -ne 'Any') { return $false }
    $a = $b | Get-NetFirewallAddressFilter
    $p = $b | Get-NetFirewallPortFilter
    $expected = '["128.0.0.0/128.0.0.0","64.0.0.0/192.0.0.0","32.0.0.0/224.0.0.0","16.0.0.0/240.0.0.0","0.0.0.0/248.0.0.0","12.0.0.0/252.0.0.0","8.0.0.0/254.0.0.0","11.0.0.0/255.0.0.0","10.128.0.0/255.128.0.0","10.64.0.0/255.192.0.0","10.32.0.0/255.224.0.0","10.16.0.0/255.240.0.0","10.8.0.0/255.248.0.0","10.4.0.0/255.252.0.0","10.0.0.0/255.254.0.0","10.2.0.0/255.255.0.0","10.3.128.0/255.255.128.0","10.3.64.0/255.255.192.0","10.3.0.0/255.255.224.0","10.3.48.0/255.255.240.0","10.3.40.0/255.255.248.0","10.3.36.0/255.255.252.0","10.3.32.0/255.255.254.0","10.3.35.0/255.255.255.0","10.3.34.128/255.255.255.128","10.3.34.64/255.255.255.192","10.3.34.32/255.255.255.224","10.3.34.16/255.255.255.240","10.3.34.0/255.255.255.248","10.3.34.12/255.255.255.252","10.3.34.8/255.255.255.254","10.3.34.10","::/1","8000::/1"]' | ConvertFrom-Json
    if ((@($a.LocalAddress) -join ',') -ne 'Any' -or (@($a.RemoteAddress | Sort-Object) -join ',') -ne (@($expected | Sort-Object) -join ',')) { return $false }
    if ([string]$p.Protocol -notin @('TCP','6')) { return $false }
    foreach ($port in $ManagedPorts) { if ([string]$port -notin @($p.LocalPort)) { return $false } }
    if ([string]($b | Get-NetFirewallApplicationFilter).Program -ne 'Any' -or [string]($b | Get-NetFirewallServiceFilter).Service -ne 'Any') { return $false }
    return $true
}
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Utf8 = New-Object Text.UTF8Encoding($false)

function Assert-IPv4([string]$Address) {
    $Parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$Parsed) -or
        $Parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $Address -in @('0.0.0.0', '255.255.255.255') -or $Address.StartsWith('127.')) {
        throw "Expected a unicast IPv4 address: $Address"
    }
}

function Get-Distros {
    if (-not (Test-Path -LiteralPath $WslExe)) { return @() }
    # Windows may provide wsl.exe before the optional platform is installed.
    # Native stderr must not abort the read-only inventory under Stop policy.
    try { $Output = & $WslExe --list --quiet 2>$null } catch { return @() }
    if ($LASTEXITCODE -ne 0) { return @() }
    @($Output | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
}

function Show-Inventory {
    $Computer = Get-CimInstance Win32_ComputerSystem
    $OperatingSystem = Get-CimInstance Win32_OperatingSystem
    [pscustomobject]@{
        Mode = 'Inspect'; RequestedStage = $Stage; Hostname = $env:COMPUTERNAME
        CurrentWindowsUser = $Identity.Name; OwnerSid = $Identity.User.Value
        Windows = $OperatingSystem.Caption; Build = $OperatingSystem.BuildNumber
        PhysicalMemoryGiB = [Math]::Round($Computer.TotalPhysicalMemory / 1GB, 1)
        CPU = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors)
        GPU = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, PNPDeviceID)
        IPv4 = @(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress, PrefixLength)
        Disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
            Select-Object DeviceID, Size, FreeSpace)
        WslDistributionsForCurrentUser = @(Get-Distros)
        SshService = @(Get-Service sshd -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType)
        Listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -in @(22, 2222, 8188, 9100, 9201, 9400) } |
            Select-Object LocalAddress, LocalPort, OwningProcess)
        Tasks = @(Get-ScheduledTask -TaskName 'GPUControl-*' -ErrorAction SilentlyContinue |
            Select-Object TaskName, State, @{ Name = 'UserId'; Expression = { $_.Principal.UserId } },
                @{ Name = 'LogonType'; Expression = { [string]$_.Principal.LogonType } })
    } | ConvertTo-Json -Depth 6
    if (Test-Path -LiteralPath $WslExe) {
        try { & $WslExe --list --verbose 2>$null } catch {
            Write-Output 'WSL_LIST_UNAVAILABLE=true. Inspect Windows WSL optional features before installation.'
        }
    }
    if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.wslconfig')) {
        Write-Output 'WSL resource configuration exists; review locally before choosing CPU/memory/disk limits.'
    }
    if ($Stage -ne 'Inspect') {
        Write-Output "No changes made. Add -Apply to execute only stage: $Stage"
    }
}

function Read-PublicKey {
    if (-not $PublicKeyPath) { throw '-PublicKeyPath must name the new host management public key (.pub).' }
    $Lines = @(Get-Content -LiteralPath $PublicKeyPath | Where-Object { $_.Trim() })
    if ($Lines.Count -ne 1 -or $Lines[0] -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+={0,3}(?: .*)?$') {
        throw 'Expected exactly one OpenSSH public key without authorized_keys options. Never supply a private key.'
    }
    $Fields = $Lines[0] -split ' ', 3
    try { [void][Convert]::FromBase64String($Fields[1]) } catch { throw 'Invalid public key encoding.' }
    # Key comments are unnecessary and may contain personal identifiers.
    return "$($Fields[0]) $($Fields[1])"
}

function Invoke-WslScript([string]$Script) {
    $Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script.Replace("`r`n", "`n")))
    & $WslExe -d $Distro -u root -- bash -c "printf '%s' '$Encoded' | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) { throw "WSL stage failed (exit $LASTEXITCODE); do not continue to the next stage." }
}

function Protect-AdminPath([string]$Path) {
    if ((Get-Item -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to install elevated maintenance code in a linked directory.'
    }
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($SidValue in @('S-1-5-32-544', 'S-1-5-18')) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $Sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
        )
        $Acl.AddAccessRule($Rule)
    }
    $Acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

if (-not $Apply -or $Stage -eq 'Inspect') { Show-Inventory; exit 0 }
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Apply requires an elevated Windows PowerShell session.'
}
$RequestedOwner = New-Object Security.Principal.NTAccount($WindowsUser)
$OwnerSid = $RequestedOwner.Translate([Security.Principal.SecurityIdentifier]).Value
if ($OwnerSid -ne $Identity.User.Value) {
    throw 'Use an elevated session of the requested Windows user; a different user cannot manage this user-owned WSL distribution.'
}
Assert-IPv4 $ControlAddress
if ($Stage -in @('WindowsSsh', 'Persistence')) {
    Assert-IPv4 $WindowsIP
    if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $WindowsIP -ErrorAction SilentlyContinue)) {
        throw 'The requested Windows IP is not assigned to this machine.'
    }
    if (-not $NodeName) { throw '-NodeName is required for this stage.' }
}
if ($Stage -in @('WslPlatform', 'WslSsh', 'Persistence')) {
    if ($Distro -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$') {
        throw 'Supply the exact -Distro name shown by wsl --list --verbose.'
    }
}

if ($Stage -eq 'WslPlatform') {
    $RebootNeeded = $false
    foreach ($FeatureName in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
        $Feature = Get-WindowsOptionalFeature -Online -FeatureName $FeatureName
        if ($Feature.State -ne 'Enabled') {
            $Result = Enable-WindowsOptionalFeature -Online -FeatureName $FeatureName -All -NoRestart
            $RebootNeeded = $RebootNeeded -or $Result.RestartNeeded -or $Feature.State -eq 'EnablePending'
        }
    }
    if ($RebootNeeded) {
        Write-Output 'REBOOT_REQUIRED=true. Features were enabled without restarting. Resume after a planned Windows restart.'
        exit 0
    }
    $ExistingDistros = @(Get-Distros)
    if ($ExistingDistros -contains $Distro) {
        Write-Output 'Distribution already exists; it has not been reinstalled, converted, or terminated.'
        & $WslExe --list --verbose
    } elseif ($InstallDistro) {
        & $WslExe --install --distribution $Distro --no-launch
        if ($LASTEXITCODE -ne 0) { throw 'WSL installation needs local resolution; existing distributions were not reset.' }
        Write-Output 'Complete the first launch of this distribution locally, then inspect and apply WslSsh.'
    } else {
        Write-Output 'Distribution is absent. To install the explicitly chosen distribution, repeat this stage with -InstallDistro.'
    }
    exit 0
}

if ($Stage -eq 'WindowsSsh') {
    $PublicKey = Read-PublicKey
    $Capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    if ($Capability.State -ne 'Installed') {
        $InstallResult = Add-WindowsCapability -Online -Name $Capability.Name
        if ($InstallResult.RestartNeeded) {
            Write-Output 'REBOOT_REQUIRED=true. OpenSSH was installed without an automatic restart.'
            exit 0
        }
    }
    $SshDirectory = Join-Path $env:ProgramData 'ssh'
    $Sshd = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'
    $SshdConfig = Join-Path $SshDirectory 'sshd_config'
    New-Item -ItemType Directory -Path $SshDirectory -Force | Out-Null
    if (-not (Test-Path -LiteralPath $SshdConfig)) {
        Copy-Item -LiteralPath (Join-Path $env:SystemRoot 'System32\OpenSSH\sshd_config_default') -Destination $SshdConfig
    }
    # A capability installation can create a LAN-wide allow rule. Disable that
    # known default; never silently rewrite unrelated custom firewall rules.
    Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    $RuleName = "GPUControl-$NodeName-WindowsSSH-From-Control"
    foreach ($Profile in @(Get-NetFirewallProfile -PolicyStore ActiveStore)) {
        if (-not $Profile.Enabled -or [string]$Profile.DefaultInboundAction -eq 'Allow') {
            throw "Firewall profile '$($Profile.Name)' must be enabled with inbound blocking before publishing SSH."
        }
    }
    $ControlOnlyBlockVerified = Test-GpuControlSourceBlock -Control $ControlAddress -ManagedPorts @(22)
    foreach ($Rule in @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow)) {
        if ($ControlOnlyBlockVerified) { continue }
        if ($Rule.Name -eq $RuleName) { continue }
        $Filter = $Rule | Get-NetFirewallPortFilter
        if ([string]$Filter.Protocol -notin @('TCP', '6', 'Any', '256')) { continue }
        $LocalPorts = @($Filter.LocalPort | ForEach-Object { [string]$_ })
        $Overlap = $LocalPorts -contains 'Any' -or $LocalPorts -contains '22'
        foreach ($PortRange in $LocalPorts) {
            if ($PortRange -match '^(\d+)-(\d+)$' -and [int]$Matches[1] -le 22 -and [int]$Matches[2] -ge 22) {
                $Overlap = $true
            }
        }
        if (-not $Overlap) { continue }
        $Program = [string]($Rule | Get-NetFirewallApplicationFilter).Program
        $Service = [string]($Rule | Get-NetFirewallServiceFilter).Service
        if ($Program -notin @('Any', 'System') -and $Program -notmatch '(?i)[\\/]sshd\.exe$') { continue }
        if ($Service -notin @('Any', 'sshd')) { continue }
        $Remote = @(($Rule | Get-NetFirewallAddressFilter).RemoteAddress)
        if ($Remote.Count -ne 1 -or $Remote[0] -ne $ControlAddress) {
            throw "Existing firewall rule '$($Rule.Name)' permits SSH from other sources; review it locally before continuing."
        }
    }
    $Original = [IO.File]::ReadAllText($SshdConfig)
    $Unmanaged = [regex]::Replace($Original, '(?ms)^# BEGIN GPUCONTROL MANAGEMENT\r?\n.*?^# END GPUCONTROL MANAGEMENT\r?\n?', '')
    $Managed = @"
# BEGIN GPUCONTROL MANAGEMENT
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
# END GPUCONTROL MANAGEMENT
"@
    $Candidate = $Managed + "`r`n" + $Unmanaged
    $ConfigChanged = $Candidate -ne $Original
    if ($ConfigChanged) {
        Copy-Item -LiteralPath $SshdConfig -Destination "$SshdConfig.gpucontrol-backup-$(Get-Date -Format yyyyMMddHHmmss)"
        [IO.File]::WriteAllText($SshdConfig, $Candidate, $Utf8)
    }
    try {
        # Generate only absent host keys; existing host identity is preserved.
        & (Join-Path $env:SystemRoot 'System32\OpenSSH\ssh-keygen.exe') -A
        if ($LASTEXITCODE -ne 0) { throw 'Could not prepare SSH host keys.' }
        & $Sshd -t -f $SshdConfig
        if ($LASTEXITCODE -ne 0) { throw 'Windows sshd configuration validation failed.' }
        $LoginName = ($Identity.Name -split '\\')[-1].ToLowerInvariant()
        $Effective = @(& $Sshd -T -f $SshdConfig -C "user=$LoginName,host=$env:COMPUTERNAME,addr=$ControlAddress")
        if ($LASTEXITCODE -ne 0) { throw 'Could not resolve effective Windows SSH configuration.' }
        $EffectivePorts = @($Effective | Where-Object { $_ -like 'port *' })
        if ($EffectivePorts.Count -ne 1 -or $EffectivePorts[0] -ne 'port 22') {
            throw 'Existing Windows SSH port differs from the dedicated Windows port 22; review locally.'
        }
        foreach ($Expected in @('pubkeyauthentication yes', 'authenticationmethods publickey', 'passwordauthentication no', 'kbdinteractiveauthentication no')) {
            if ($Effective -notcontains $Expected) { throw 'Existing Match configuration overrides required key-only management; review locally.' }
        }
        $KeySetting = @($Effective | Where-Object { $_ -like 'authorizedkeysfile *' })
        if ($KeySetting.Count -ne 1) { throw 'Could not resolve authorized_keys location.' }
        $KeyLocation = $KeySetting[0].Substring('authorizedkeysfile '.Length).Trim()
        $AdminKeys = $KeyLocation -match '^(?:__PROGRAMDATA__|[a-zA-Z]:[/\\]ProgramData)[/\\]ssh[/\\]administrators_authorized_keys$'
        if ($AdminKeys) {
            $AuthorizedKeys = Join-Path $SshDirectory 'administrators_authorized_keys'
        } elseif ($KeyLocation -in @('.ssh/authorized_keys', '.ssh\\authorized_keys', '%h/.ssh/authorized_keys')) {
            $UserSsh = Join-Path $env:USERPROFILE '.ssh'
            New-Item -ItemType Directory -Path $UserSsh -Force | Out-Null
            if ((Get-Item -LiteralPath $UserSsh).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Refusing to write authorized_keys through a linked user SSH directory.'
            }
            $AuthorizedKeys = Join-Path $UserSsh 'authorized_keys'
        } else {
            throw 'Custom authorized_keys path detected; resolve its ownership and path before applying.'
        }
        if ((Test-Path -LiteralPath $AuthorizedKeys) -and
            ((Get-Item -LiteralPath $AuthorizedKeys).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Refusing to write a linked authorized_keys file.'
        }
        $ExistingKeys = @()
        if (Test-Path -LiteralPath $AuthorizedKeys) { $ExistingKeys = @(Get-Content -LiteralPath $AuthorizedKeys) }
        $KeyAlreadyPresent = @($ExistingKeys | Where-Object { $_ -eq $PublicKey -or $_.StartsWith("$PublicKey ") }).Count -gt 0
        if (-not $KeyAlreadyPresent) {
            [IO.File]::WriteAllLines($AuthorizedKeys, [string[]]($ExistingKeys + $PublicKey), $Utf8)
        }
        $KeyAcl = New-Object Security.AccessControl.FileSecurity
        $KeyAcl.SetAccessRuleProtection($true, $false)
        $AllowedSids = @('S-1-5-32-544', 'S-1-5-18')
        $KeyOwnerSid = 'S-1-5-32-544'
        if (-not $AdminKeys) { $AllowedSids += $OwnerSid; $KeyOwnerSid = $OwnerSid }
        foreach ($SidValue in $AllowedSids) {
            $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
            $KeyAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($Sid, 'FullControl', 'Allow')))
        }
        $KeyAcl.SetOwner((New-Object Security.Principal.SecurityIdentifier($KeyOwnerSid)))
        Set-Acl -LiteralPath $AuthorizedKeys -AclObject $KeyAcl
    } catch {
        if ($ConfigChanged) { [IO.File]::WriteAllText($SshdConfig, $Original, $Utf8) }
        throw
    }
    Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name $RuleName -DisplayName $RuleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalAddress $WindowsIP -LocalPort 22 -RemoteAddress $ControlAddress -Profile Any | Out-Null
    Set-Service sshd -StartupType Automatic
    $Service = Get-Service sshd
    if ($Service.Status -eq 'Running' -and $ConfigChanged) {
        Write-Output 'SSH_CONFIG_RELOAD_REQUIRED=true. Existing SSH service was not restarted. Restart sshd locally after checking active management sessions.'
        Write-Output 'The new key-only policy takes effect after that service restart; do not treat this stage as accepted until then.'
    } elseif ($Service.Status -ne 'Running') {
        Start-Service sshd
    }
    Write-Output "Windows SSH prepared: $WindowsIP`:22; source=$ControlAddress; user=$($Identity.Name). Verify public-key login from the control host."
    exit 0
}

if (@(Get-Distros) -notcontains $Distro) { throw 'The named distribution is not registered for this Windows user.' }
if ($Stage -eq 'WslSsh') {
    if (-not $WslUser -or $WslUser -eq 'root') { throw 'Supply a non-root Linux management account with -WslUser.' }
    $PublicKey = Read-PublicKey
    $KeyBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($PublicKey))
    $SudoValue = '0'
    if ($EnableWslSudo) { $SudoValue = '1' }
    $Script = @'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! uname -r | grep -qi 'microsoft.*WSL2'; then
    echo 'This stage requires WSL2; do not convert a pre-existing distribution without reviewing it.' >&2
    exit 20
fi
if [ "$(ps -p 1 -o comm=)" != systemd ]; then
    python3 - <<'PY'
import pathlib, re, shutil, time
p = pathlib.Path('/etc/wsl.conf')
old = p.read_text() if p.exists() else ''
lines = old.splitlines(keepends=True)
start = next((i for i, x in enumerate(lines) if re.match(r'^\s*\[boot\]\s*$', x)), None)
if start is None:
    new = old.rstrip() + '\n\n[boot]\nsystemd=true\n'
else:
    end = next((i for i in range(start + 1, len(lines)) if re.match(r'^\s*\[', lines[i])), len(lines))
    match = next((i for i in range(start + 1, end) if re.match(r'^\s*systemd\s*=', lines[i])), None)
    if match is None:
        lines.insert(start + 1, 'systemd=true\n')
    else:
        lines[match] = 'systemd=true\n'
    new = ''.join(lines)
if old != new:
    if p.exists(): shutil.copy2(p, str(p) + '.gpucontrol-backup-' + str(int(time.time())))
    p.write_text(new)
print('WSL_RESTART_REQUIRED=true. systemd was configured; schedule a distro restart locally and repeat WslSsh.')
PY
    exit 21
fi
apt-get update
apt-get install -y --no-install-recommends openssh-server sudo ca-certificates
management_user='__WSL_USER__'
if ! id "$management_user" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$management_user"
fi
if [ "$(id -u "$management_user")" -lt 1000 ]; then
    echo 'Refusing to repurpose a system account as the SSH management user.' >&2
    exit 22
fi
python3 - "$management_user" '__KEY_BASE64__' <<'PY'
import base64, os, pathlib, pwd, sys
account = pwd.getpwnam(sys.argv[1])
if account.pw_shell in ('/bin/false', '/usr/sbin/nologin', '/sbin/nologin'):
    raise SystemExit('Existing management user has a non-login shell; review locally.')
directory = pathlib.Path(account.pw_dir) / '.ssh'
path = directory / 'authorized_keys'
if directory.is_symlink() or path.is_symlink(): raise SystemExit('Refusing linked SSH paths.')
directory.mkdir(mode=0o700, exist_ok=True)
os.chown(directory, account.pw_uid, account.pw_gid)
os.chmod(directory, 0o700)
key = base64.b64decode(sys.argv[2]).decode('ascii')
lines = path.read_text().splitlines() if path.exists() else []
if not any(line == key or line.startswith(key + ' ') for line in lines):
    lines.append(key)
    path.write_text('\n'.join(lines) + '\n')
os.chown(path, account.pw_uid, account.pw_gid)
os.chmod(path, 0o600)
PY
if [ '__SUDO__' = 1 ]; then
    sudo_path="/etc/sudoers.d/90-gpucontrol-${management_user}"
    sudo_candidate=$(mktemp)
    printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$management_user" > "$sudo_candidate"
    visudo -cf "$sudo_candidate"
    install -o root -g root -m 0440 "$sudo_candidate" "$sudo_path"
    rm -f "$sudo_candidate"
fi
install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d /run/sshd
config=/etc/ssh/sshd_config.d/00-gpucontrol-management.conf
backup=$(mktemp)
had_config=0
if [ -f "$config" ]; then cp -p "$config" "$backup"; had_config=1; fi
cat > "$config" <<'EOF'
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
chmod 0644 "$config"
if ! /usr/sbin/sshd -t || ! /usr/sbin/sshd -T -C "user=$management_user,host=localhost,addr=__CONTROL_IP__" | python3 -c '
import sys
lines=set(sys.stdin.read().splitlines())
expected={"port 22","pubkeyauthentication yes","authenticationmethods publickey","passwordauthentication no","kbdinteractiveauthentication no","permitrootlogin no"}
sys.exit(0 if expected <= lines else 1)
'; then
    if [ "$had_config" = 1 ]; then cp -p "$backup" "$config"; else rm -f "$config"; fi
    rm -f "$backup"
    echo 'SSH configuration failed effective validation; managed SSH config was restored.' >&2
    exit 23
fi
rm -f "$backup"
systemctl enable ssh
if systemctl is-active --quiet ssh; then systemctl reload ssh; else systemctl start ssh; fi
printf 'WSL_SSH_READY=true user=%s\n' "$management_user"
'@
    $Script = $Script.Replace('__WSL_USER__', $WslUser).Replace('__KEY_BASE64__', $KeyBase64).
        Replace('__SUDO__', $SudoValue).Replace('__CONTROL_IP__', $ControlAddress)
    Invoke-WslScript $Script
    exit 0
}

if ($Stage -eq 'Persistence') {
    if ($RuntimePorts -contains 22 -or $RuntimePorts -contains 2222 -or
        @($RuntimePorts | Select-Object -Unique).Count -ne $RuntimePorts.Count) {
        throw 'RuntimePorts must be unique and must not include the management ports 22 or 2222.'
    }
    $SourceProxy = Join-Path $PSScriptRoot 'Update-GPUControlWslProxy.ps1'
    if (-not (Test-Path -LiteralPath $SourceProxy)) { throw 'The companion Update-GPUControlWslProxy.ps1 script is missing.' }
    $InstallDirectory = Join-Path $env:ProgramData "GPUControl\$NodeName"
    New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
    Protect-AdminPath $InstallDirectory
    $ConfigPath = Join-Path $InstallDirectory 'proxy-config.json'
    $ProxyPath = Join-Path $InstallDirectory 'Update-GPUControlWslProxy.ps1'
    $Mappings = @([pscustomobject]@{ ListenPort = 2222; ConnectPort = 22 }) + @(
        $RuntimePorts | ForEach-Object { [pscustomobject]@{ ListenPort = $_; ConnectPort = $_ } }
    )
    $Previous = $null
    if (Test-Path -LiteralPath $ConfigPath) {
        $Previous = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        if ($Previous.WindowsIP -ne $WindowsIP -or $Previous.Distro -ne $Distro -or
            $Previous.OwnerSid -ne $OwnerSid -or $Previous.ControlAddress -ne $ControlAddress) {
            throw 'Existing installed identity/address differs; reconcile the old mappings before changing the configuration.'
        }
        foreach ($Mapping in $Previous.Mappings) {
            if (@($Mappings | Where-Object {
                $_.ListenPort -eq $Mapping.ListenPort -and $_.ConnectPort -eq $Mapping.ConnectPort
            }).Count -ne 1) { throw 'Removing existing mappings requires an explicit maintenance operation; supply all existing ports.' }
        }
    }
    $TaskName = "GPUControl-$NodeName-WSL-Maintainer"
    $PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}" -Apply -Watch' -f $ProxyPath, $ConfigPath
    $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($ExistingTask -and $ExistingTask.State -eq 'Running') {
        $ExistingTaskOwnerSid = [string]$ExistingTask.Principal.UserId
        if ($ExistingTaskOwnerSid -notmatch '^S-1-') {
            $ExistingTaskOwnerSid = (New-Object Security.Principal.NTAccount($ExistingTaskOwnerSid)).Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        }
        $SamePorts = $null -ne $Previous -and
            (@($Previous.Mappings.ListenPort | Sort-Object) -join ',') -eq (@($Mappings.ListenPort | Sort-Object) -join ',')
        $SameScript = (Test-Path -LiteralPath $ProxyPath) -and
            (Get-FileHash -LiteralPath $SourceProxy -Algorithm SHA256).Hash -eq
            (Get-FileHash -LiteralPath $ProxyPath -Algorithm SHA256).Hash
        $SameTask = [string]$ExistingTask.Principal.LogonType -eq 'Interactive' -and
            $ExistingTaskOwnerSid -eq $OwnerSid -and
            @($ExistingTask.Actions).Count -eq 1 -and $ExistingTask.Actions[0].Execute -eq $PowerShellExe -and
            $ExistingTask.Actions[0].Arguments -eq $Arguments
        if ($SamePorts -and $SameScript -and $SameTask) {
            Write-Output "PERSISTENCE_ALREADY_RUNNING=true task=$TaskName. No task or WSL restart was performed."
            exit 0
        }
        throw 'The existing maintainer is running. Updating it needs a maintenance window with all workloads drained; do not terminate WSL.'
    }
    Copy-Item -LiteralPath $SourceProxy -Destination $ProxyPath -Force
    [pscustomobject]@{
        NodeName = $NodeName; WindowsIP = $WindowsIP; ControlAddress = $ControlAddress
        Distro = $Distro; OwnerSid = $OwnerSid; Mappings = $Mappings
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    & $ProxyPath -ConfigPath $ConfigPath -Apply
    $Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity.Name
    $TaskPrincipal = New-ScheduledTaskPrincipal -UserId $Identity.Name -LogonType Interactive -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $TaskPrincipal `
        -Settings $Settings -Description 'Maintain this user-owned WSL instance and control-only Windows NAT mappings.' -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "PERSISTENCE_CONFIGURED=true task=$TaskName owner=$($Identity.Name) logon_type=Interactive"
    Write-Output 'The owner must log in after a Windows reboot. Recovery before login requires a separately verified local Task Scheduler credential configuration.'
}
