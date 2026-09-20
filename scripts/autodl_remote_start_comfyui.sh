#!/bin/bash
set -euo pipefail

readonly RUNTIME_ROOT=/root/autodl-tmp/gpu-control/modelview-inpaint-normal-2step-r1
readonly LOCK_FILE=/root/autodl-tmp/gpu-control/start-comfyui.lock
readonly PID_FILE="${RUNTIME_ROOT}/evidence/comfyui.pid"
readonly LOG_FILE="${RUNTIME_ROOT}/evidence/comfyui.log"
readonly STARTING_GRACE_SECONDS=180
readonly TERMINATE_GRACE_SECONDS=15
readonly STARTUP_CRASH_WINDOW_SECONDS=10
declare -ar COMFY_COMMAND=(
  /root/miniconda3/bin/python
  main.py
  --port 6006
  --listen 127.0.0.1
  --reserve-vram 3.0
  --disable-async-offload
  --disable-pinned-memory
)

system_stats_ready() {
  local status
  status="$(
    /usr/bin/curl --silent --show-error --output /dev/null \
      --max-time 3 --write-out '%{http_code}' \
      http://127.0.0.1:6006/system_stats 2>/dev/null || true
  )"
  [[ "${status}" == 200 ]]
}

is_exact_comfy_process() {
  local pid="$1"
  local index
  local -a argv=()
  [[ "${pid}" =~ ^[0-9]+$ && -r "/proc/${pid}/cmdline" ]] || return 1
  mapfile -d '' -t argv < "/proc/${pid}/cmdline" || return 1
  [[ "${#argv[@]}" -eq "${#COMFY_COMMAND[@]}" ]] || return 1
  for index in "${!COMFY_COMMAND[@]}"; do
    [[ "${argv[index]}" == "${COMFY_COMMAND[index]}" ]] || return 1
  done
}

exact_comfy_pids() {
  local process pid
  for process in /proc/[0-9]*; do
    pid="${process##*/}"
    if is_exact_comfy_process "${pid}"; then
      printf '%s\n' "${pid}"
    fi
  done
}

process_age_seconds() {
  local age
  age="$(/usr/bin/ps -o etimes= -p "$1" 2>/dev/null | /usr/bin/tr -d '[:space:]')"
  [[ "${age}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${age}"
}

terminate_exact_processes() {
  local pid deadline alive
  local -a pids=("$@")
  for pid in "${pids[@]}"; do
    if is_exact_comfy_process "${pid}"; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done

  deadline=$((SECONDS + TERMINATE_GRACE_SECONDS))
  while ((SECONDS < deadline)); do
    alive=0
    for pid in "${pids[@]}"; do
      if is_exact_comfy_process "${pid}"; then
        alive=1
        break
      fi
    done
    ((alive == 0)) && return 0
    sleep 1
  done

  for pid in "${pids[@]}"; do
    if is_exact_comfy_process "${pid}"; then
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
  sleep 1
  for pid in "${pids[@]}"; do
    if is_exact_comfy_process "${pid}"; then
      return 1
    fi
  done
}

mkdir -p "${RUNTIME_ROOT}/evidence"
exec 9>"${LOCK_FILE}"
if ! /usr/bin/flock --exclusive --wait 30 9; then
  echo "ComfyUI startup lock timed out" >&2
  exit 1
fi

# Recheck health only after taking the lock. This makes simultaneous AutoDL
# power_on bootstrap and tunnel recovery calls serialize safely.
if system_stats_ready; then
  exit 0
fi

mapfile -t existing_pids < <(exact_comfy_pids)
if (("${#existing_pids[@]}" > 0)); then
  for pid in "${existing_pids[@]}"; do
    if ! age="$(process_age_seconds "${pid}")"; then
      # Fail safe: never terminate a process whose identity or age became
      # uncertain while it was inspected.
      exit 0
    fi
    if ((age < STARTING_GRACE_SECONDS)); then
      exit 0
    fi
  done
  terminate_exact_processes "${existing_pids[@]}"
fi

cd /root/ComfyUI
# The startup lock belongs to this short-lived bootstrap process only. Without
# explicitly closing fd 9, the background ComfyUI process inherits the flock
# and every later idempotent recovery call blocks until its timeout.
nohup "${COMFY_COMMAND[@]}" >> "${LOG_FILE}" 2>&1 < /dev/null 9>&- &
pid=$!
printf '%s\n' "${pid}" > "${PID_FILE}"

# ComfyUI may need considerably longer to become HTTP-ready, but a process
# that exits during this short window is an immediate startup failure.
deadline=$((SECONDS + STARTUP_CRASH_WINDOW_SECONDS))
while ((SECONDS < deadline)); do
  if ! is_exact_comfy_process "${pid}"; then
    wait "${pid}" || status=$?
    echo "ComfyUI exited during startup (status ${status:-0})" >&2
    exit 1
  fi
  sleep 1
done

is_exact_comfy_process "${pid}"
