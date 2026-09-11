# 5070 Ti Windows deployment baseline

Read-only snapshot before extending GPU Control runtime ports. The new machine is
`10.3.34.18`, Windows owner `LILITHG-N0K4M0L\lilithgames`, WSL distribution
`Ubuntu-22.04`, Linux SSH management user `gpucontrol`.

- `Update-GPUControlWslProxy.ps1`, `proxy-config.json`, `proxy-ownership.json` and
  `proxy.log` are byte-for-byte copies of the installed host files.
- `adapted/` contains the two locally adapted onboarding scripts. The installed
  proxy script matches the adapted proxy script by SHA-256. It includes the
  single-address array fix and source-block verification added during preprocessing.
- `maintainer-task.xml` is the existing task definition. It uses the distribution
  owner's InteractiveToken, highest run level, a login trigger, and a continuous
  watchdog/keepalive; it is not an unattended pre-login startup guarantee.
- `inventory-initial.json`, `files-and-task.json`, `adapted-and-software.json`
  capture task, proxy, firewall, ACL and installation presence evidence. No SSH
  private keys, passwords, model credentials or licence contents were copied.
- `ssh-stability.json`: Windows SSH and WSL SSH each passed 5/5 authenticated
  checks with strict host-key verification; WSL sudo was noninteractive and the
  kernel boot ID remained constant.
- Windows physical Ethernet is DHCP-configured at `10.3.34.18`, MAC
  `34-5A-60-4B-2B-3D`, link 1 Gbps. A DHCP reservation cannot be established from
  this host snapshot; no network settings were changed.
- Registry, Program Files and common portable directories did not show Blender,
  Ministry of Flat, or Substance Automation Toolkit installations. Common SAT
  licence directories were absent. This is presence evidence, not a licence
  validation, and it does not authorize copying another machine's licence.

The initial source-block rule was mathematically checked to cover every IPv4
source except `10.3.34.11`, and every IPv6 source, for management ports 22/2222.
The reviewed additive deployment expands only its port list, keeps this source
set, and gives runtime port proxies their own config, state and task.
