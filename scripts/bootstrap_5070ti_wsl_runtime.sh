#!/usr/bin/env bash
# Prepare only the NEW WSL host's container runtime. No production registration.
set -Eeuo pipefail

usage() {
  cat <<'TXT'
Usage: bootstrap_5070ti_wsl_runtime.sh [--inspect]
       sudo bootstrap_5070ti_wsl_runtime.sh --apply --linux-user USER --lock FILE

Inspect is read-only. Apply installs the reviewed Jammy/amd64 Docker and NVIDIA
Container Toolkit package set after SHA-256 verification. It does not install a
Linux GPU driver, start application containers, copy credentials or workflows,
register a cluster node, or restart Windows/WSL. Existing Docker containers are
not supported by this fresh-host bootstrap; inspect them separately first.
TXT
}

mode=inspect
linux_user=""
script_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
lock_file="${script_root}/deploy/gpu-node/5070ti/runtime-lock.json"
while (($#)); do
  case "$1" in
    --inspect) mode=inspect; shift ;;
    --apply) mode=apply; shift ;;
    --linux-user) linux_user="${2:?--linux-user requires a value}"; shift 2 ;;
    --lock) lock_file="${2:?--lock requires a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if [[ "$mode" == inspect ]]; then
  python3 - <<'PY'
import json, pathlib, subprocess

def run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return {"exit_code": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}

gpu = "/usr/lib/wsl/lib/nvidia-smi"
data = {
    "kernel": run(["uname", "-r"]),
    "os_release": pathlib.Path("/etc/os-release").read_text(),
    "pid1": pathlib.Path("/proc/1/comm").read_text().strip(),
    "cpu": run(["getconf", "_NPROCESSORS_ONLN"]),
    "memory": run(["free", "-m"]),
    "filesystem": run(["df", "-hT", "/"]),
    "gpu": run([gpu, "--query-gpu=name,uuid,driver_version,memory.total,memory.free", "--format=csv"]),
    "docker": run(["docker", "version", "--format", "{{.Server.Version}}"]),
    "containers": run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"]),
}
print(json.dumps(data, indent=2))
PY
  exit 0
fi

[[ "$EUID" -eq 0 ]] || { echo "Apply requires root inside the NEW WSL distribution." >&2; exit 1; }
[[ "$linux_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Provide an existing Linux user." >&2; exit 2; }
id "$linux_user" >/dev/null
[[ "$(id -u "$linux_user")" -ne 0 ]] || { echo "The management user must not be root." >&2; exit 1; }
[[ -r "$lock_file" ]] || { echo "Runtime lock file is missing." >&2; exit 1; }
grep -qi microsoft /proc/sys/kernel/osrelease || { echo "Refusing: this is not WSL." >&2; exit 1; }
[[ "$(cat /proc/1/comm)" == systemd ]] || { echo "Enable WSL systemd before applying." >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_CODENAME" == jammy ]] || { echo "Reviewed baseline requires Ubuntu 22.04 (jammy)." >&2; exit 1; }
[[ "$(dpkg --print-architecture)" == amd64 ]] || { echo "Reviewed packages require amd64." >&2; exit 1; }

# This is deliberately a fresh-host installer. Never wake an existing daemon,
# even when its CLI was removed or points at another machine through a context.
for state_directory in /var/lib/docker /var/lib/containerd; do
  if [[ -d "$state_directory" && -n "$(ls -A "$state_directory")" ]]; then
    echo "Existing container runtime data: $state_directory. Run inspect and use an incremental deployment; do not delete it." >&2
    exit 1
  fi
done
for config_file in /etc/docker/daemon.json /etc/containerd/config.toml; do
  if [[ -s "$config_file" ]]; then
    echo "Existing runtime configuration: $config_file. Review custom data paths and workloads before deployment." >&2
    exit 1
  fi
done
if command -v docker >/dev/null 2>&1 && [[ -S /var/run/docker.sock ]]; then
  if ! existing_containers=$(docker --host unix:///var/run/docker.sock ps -aq); then
    echo "Cannot inspect the local Docker daemon; refusing to proceed." >&2
    exit 1
  fi
  [[ -z "$existing_containers" ]] || { echo "Existing local containers found; use an incremental deployment." >&2; exit 1; }
fi

python3 - "$lock_file" <<'PY'
import json, re, sys
lock = json.load(open(sys.argv[1]))
assert lock["ubuntu_codename"] == "jammy" and lock["architecture"] == "amd64"
allowed = {"docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin",
           "nvidia-container-toolkit", "nvidia-container-toolkit-base", "libnvidia-container-tools", "libnvidia-container1"}
assert len(lock["packages"]) == len(allowed)
assert {p["name"] for p in lock["packages"]} == allowed
for p in lock["packages"]:
    assert re.fullmatch(r"[0-9a-f]{64}", p["sha256"])
    assert re.fullmatch(r"[A-Za-z0-9:.+~_-]+", p["version"])
PY

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg jq rsync openssh-client python3-venv
install -d -m 0755 /etc/apt/keyrings

# Preserve existing APT definitions; avoid duplicate/conflicting vendor entries.
docker_source=/etc/apt/sources.list.d/gpu-control-5070ti-docker.list
nvidia_source=/etc/apt/sources.list.d/gpu-control-5070ti-nvidia.list
if ! grep -Rqs 'download.docker.com/linux/ubuntu' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/gpu-control-docker.asc
  chmod 0644 /etc/apt/keyrings/gpu-control-docker.asc
  printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/gpu-control-docker.asc] https://download.docker.com/linux/ubuntu jammy stable' > "$docker_source"
fi
if ! grep -Rqs 'nvidia.github.io/libnvidia-container' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  key_download=$(mktemp)
  curl --fail --silent --show-error --location https://nvidia.github.io/libnvidia-container/gpgkey -o "$key_download"
  gpg --batch --yes --dearmor -o /etc/apt/keyrings/gpu-control-nvidia.gpg "$key_download"
  rm -f -- "$key_download"
  chmod 0644 /etc/apt/keyrings/gpu-control-nvidia.gpg
  printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/gpu-control-nvidia.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /' > "$nvidia_source"
fi
apt-get update

package_dir=$(mktemp -d /var/tmp/gpu-control-5070ti-packages.XXXXXXXX)
trap 'rm -rf -- "$package_dir"' EXIT
mapfile -t package_specs < <(python3 - "$lock_file" <<'PY'
import json, sys
for p in json.load(open(sys.argv[1]))["packages"]:
    print(p["name"] + "=" + p["version"])
PY
)
(
  cd "$package_dir"
  apt-get download "${package_specs[@]}"
)
python3 - "$lock_file" "$package_dir" <<'PY'
import hashlib, json, pathlib, subprocess, sys
expected = {p["name"]: p for p in json.load(open(sys.argv[1]))["packages"]}
found = set()
for f in pathlib.Path(sys.argv[2]).glob("*.deb"):
    name = subprocess.check_output(["dpkg-deb", "-f", str(f), "Package"], text=True).strip()
    version = subprocess.check_output(["dpkg-deb", "-f", str(f), "Version"], text=True).strip()
    arch = subprocess.check_output(["dpkg-deb", "-f", str(f), "Architecture"], text=True).strip()
    if name not in expected or name in found:
        raise SystemExit("Unexpected or duplicate package: " + name)
    p = expected[name]
    if version != p["version"] or arch != "amd64" or hashlib.sha256(f.read_bytes()).hexdigest() != p["sha256"]:
        raise SystemExit("Package version/architecture/SHA-256 mismatch: " + name)
    found.add(name)
    print("VERIFIED", name, version)
if found != set(expected):
    raise SystemExit("Missing packages: " + repr(set(expected) - found))
PY

apt-get install -y --no-install-recommends "$package_dir"/*.deb
install -d -m 0755 /etc/docker
if [[ -f /etc/docker/daemon.json ]]; then
  cp -a -- /etc/docker/daemon.json "/etc/docker/daemon.json.pre-5070ti-$(date -u +%Y%m%dT%H%M%S%N)"
fi
nvidia-ctk runtime configure --runtime=docker
# This bootstrap has already refused all pre-existing containers.
systemctl enable containerd docker
systemctl restart docker
usermod -aG docker "$linux_user"
deploy_group="$(id -gn "$linux_user")"
install -d -o "$linux_user" -g "$deploy_group" /opt/gpu-control /srv/gpu-control/images
for directory in input output temp user user/default user/default/workflows; do
  install -d -o 10001 -g 10001 "/srv/comfyui/runtime/$directory"
done
python3 - "$lock_file" <<'PY'
import json, subprocess, sys
for p in json.load(open(sys.argv[1]))["packages"]:
    actual = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", p["name"]], text=True).strip()
    if actual != p["version"]:
        raise SystemExit("Installed version mismatch: " + p["name"])
print("LOCKED_PACKAGES_VERIFIED")
PY
docker --host unix:///var/run/docker.sock version --format '{{.Server.Version}}'
docker --host unix:///var/run/docker.sock compose version
echo "RUNTIME_PREPARED: reconnect SSH to refresh docker group membership. No application or cluster registration was started."
