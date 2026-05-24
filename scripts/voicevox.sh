#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${VOICEVOX_CONTAINER:-orbit-ai-voicevox}"
IMAGE="${VOICEVOX_IMAGE:-voicevox/voicevox_engine:cpu-latest}"
PORT="${VOICEVOX_PORT:-50021}"
LEGACY_CONTAINER="colleague-ai-voicevox"

usage() {
  cat <<EOF
Usage: $0 {up|down|restart|status|logs}
EOF
}

is_created() {
  docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"
}

is_running() {
  docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"
}

api_ready() {
  curl -fsS "http://127.0.0.1:${PORT}/version" >/dev/null 2>&1
}

remove_if_exists() {
  local name="$1"
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null
  fi
}

case "${1:-}" in
  up)
    if is_running; then
      echo "VOICEVOX already running: $CONTAINER"
    elif api_ready; then
      echo "VOICEVOX API already available on 127.0.0.1:${PORT}"
    elif is_created; then
      docker start "$CONTAINER"
    else
      remove_if_exists "$CONTAINER"
      remove_if_exists "$LEGACY_CONTAINER"
      docker run -d --name "$CONTAINER" -p "127.0.0.1:${PORT}:50021" "$IMAGE"
    fi
    ;;
  down)
    if is_created; then
      docker stop "$CONTAINER" >/dev/null
    else
      echo "VOICEVOX container not found: $CONTAINER"
    fi
    ;;
  restart)
    "$0" down
    "$0" up
    ;;
  status)
    docker ps -a --filter "name=^${CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    curl -fsS "http://127.0.0.1:${PORT}/version" || true
    ;;
  logs)
    docker logs -f "$CONTAINER"
    ;;
  *)
    usage
    exit 2
    ;;
esac
