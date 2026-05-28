#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${ORBIT_AI_LOG_FILE:-${ROOT_DIR}/logs/orbit-ai.log}"
RESTART_DELAY="${ORBIT_AI_RESTART_DELAY:-5}"
DAEMON_COMMAND="${ORBIT_AI_DAEMON_COMMAND:-make run}"
RESTART_HOOK="${ORBIT_AI_RESTART_HOOK:-}"

running=1
child_pid=""
sleep_pid=""

if ! [[ "${RESTART_DELAY}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  printf 'ORBIT_AI_RESTART_DELAY must be a non-negative number: %s\n' "${RESTART_DELAY}" >&2
  exit 2
fi

stop() {
  running=0
  if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" 2>/dev/null; then
    kill "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
  fi
  if [[ -n "${sleep_pid}" ]] && kill -0 "${sleep_pid}" 2>/dev/null; then
    kill "${sleep_pid}" 2>/dev/null || true
    wait "${sleep_pid}" 2>/dev/null || true
  fi
}

trap stop INT TERM

mkdir -p "$(dirname "${LOG_FILE}")"
touch "${LOG_FILE}"
cd "${ROOT_DIR}"

printf '[%s] orbit-ai daemon started: command=%s log=%s restart_delay=%ss\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${DAEMON_COMMAND}" "${LOG_FILE}" "${RESTART_DELAY}" >>"${LOG_FILE}"

while [[ "${running}" -eq 1 ]]; do
  printf '[%s] starting orbit-ai\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" >>"${LOG_FILE}"

  bash -c "exec ${DAEMON_COMMAND}" >>"${LOG_FILE}" 2>&1 &
  child_pid=$!
  wait "${child_pid}"
  status=$?
  child_pid=""

  if [[ "${running}" -ne 1 ]]; then
    break
  fi

  printf '[%s] orbit-ai exited: status=%s; restarting in %ss\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${status}" "${RESTART_DELAY}" >>"${LOG_FILE}"

  if [[ -n "${RESTART_HOOK}" ]]; then
    ORBIT_AI_EXIT_STATUS="${status}" bash -c "${RESTART_HOOK}" >>"${LOG_FILE}" 2>&1 || true
  fi

  sleep "${RESTART_DELAY}" &
  sleep_pid=$!
  wait "${sleep_pid}" || true
  sleep_pid=""
done

printf '[%s] orbit-ai daemon stopped\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" >>"${LOG_FILE}"
