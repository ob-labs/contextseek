#!/usr/bin/env bash
# Restart ContextSeek in a user-like single-origin mode.
#
# Defaults:
#   app: http://127.0.0.1:8000
#
# Overrides:
#   BACKEND_PORT=8001 ./restart-dev.sh
#   CLEAR_PROXY_FOR_BACKEND=0 ./restart-dev.sh

if [ -z "${RESTART_DEV_BASH_REEXEC:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    RESTART_DEV_BASH_REEXEC=1 exec bash "$0" "$@"
  fi
  echo "restart-dev.sh requires bash. Run: ./restart-dev.sh or bash restart-dev.sh" >&2
  exit 2
fi

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
CLEAR_PROXY_FOR_BACKEND="${CLEAR_PROXY_FOR_BACKEND:-1}"

LOG_DIR="${LOG_DIR:-${ROOT}/.dev-logs}"
PID_DIR="${PID_DIR:-${ROOT}/.dev-pids}"
VENV_DIR="${VENV_DIR:-${ROOT}/.venv}"

mkdir -p "$LOG_DIR" "$PID_DIR"

log() {
  printf '[restart-dev] %s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

pids_on_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN -n -P 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser "${port}/tcp" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
  else
    die "need lsof or fuser to detect occupied ports"
  fi
}

kill_port() {
  local port="$1"
  local -a pids
  mapfile -t pids < <(pids_on_port "$port" | sort -u)
  if ((${#pids[@]} == 0)); then
    log "port ${port} is free"
    return
  fi

  log "port ${port} is occupied by PID(s): ${pids[*]}; stopping"
  kill -TERM "${pids[@]}" 2>/dev/null || true

  for _ in {1..20}; do
    mapfile -t pids < <(pids_on_port "$port" | sort -u)
    ((${#pids[@]} == 0)) && {
      log "port ${port} released"
      return
    }
    sleep 0.25
  done

  log "port ${port} still occupied by PID(s): ${pids[*]}; force killing"
  kill -KILL "${pids[@]}" 2>/dev/null || true
}

project_backend_pids() {
  local self="$$"
  ps -eo pid=,args= |
    awk -v self="$self" '
      (/contextseek\.http\.server:app/ || /contextseek desktop-server/) && $1 != self { print $1 }
    ' |
    sort -u
}

kill_project_backend() {
  local -a pids
  mapfile -t pids < <(project_backend_pids)
  if ((${#pids[@]} == 0)); then
    log "no stale contextseek backend processes found"
    return
  fi

  log "stopping stale contextseek backend PID(s): ${pids[*]}"
  kill -TERM "${pids[@]}" 2>/dev/null || true

  for _ in {1..20}; do
    mapfile -t pids < <(project_backend_pids)
    ((${#pids[@]} == 0)) && {
      log "stale contextseek backend processes stopped"
      return
    }
    sleep 0.25
  done

  log "stale contextseek backend PID(s) still alive: ${pids[*]}; force killing"
  kill -KILL "${pids[@]}" 2>/dev/null || true
}

ensure_python_env() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    return
  fi

  command -v uv >/dev/null 2>&1 || die "missing ${VENV_DIR}/bin/python and uv is not installed"
  log "virtualenv not found; creating with uv sync --extra http --extra seekdb"
  (cd "$ROOT" && uv sync --extra http --extra seekdb)
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local timeout="${3:-45}"
  local log_file="$4"

  log "waiting for ${name}: ${url}"
  for ((i = 1; i <= timeout; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "${name} is ready"
      return
    fi
    sleep 1
  done

  log "${name} did not become ready after ${timeout}s; last log lines:"
  tail -n 80 "$log_file" || true
  exit 1
}

start_detached() {
  local log_file="$1"
  shift

  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$@" >"$log_file" 2>&1 </dev/null &
  else
    nohup "$@" >"$log_file" 2>&1 </dev/null &
  fi
  echo "$!"
}

start_app() {
  local log_file="${LOG_DIR}/app.log"
  local pid
  : >"$log_file"
  log "building dashboard for same-origin serving"
  (
    cd "$ROOT"
    npm --prefix dashboard install
    VITE_CTX_BASE="" npm --prefix dashboard run build
  ) >>"$log_file" 2>&1

  log "starting app on http://${BACKEND_HOST}:${BACKEND_PORT} (log: ${log_file})"

  pid="$(
    start_detached "$log_file" bash -c '
    set -Eeuo pipefail
    cd "$1"
    # shellcheck disable=SC1091
    source "$2/bin/activate"
    export PYTHONPATH=src
    if [[ "$7" == "1" ]]; then
      unset ALL_PROXY all_proxy HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy
    fi
    exec contextseek desktop-server \
      --host "$3" \
      --port "$4"
  ' _ "$ROOT" "$VENV_DIR" "$BACKEND_HOST" "$BACKEND_PORT" "$LOG_DIR" "$PID_DIR" "$CLEAR_PROXY_FOR_BACKEND"
  )"

  echo "$pid" >"${PID_DIR}/app.pid"
}

main() {
  log "root: ${ROOT}"
  ensure_python_env

  kill_project_backend
  kill_port "$BACKEND_PORT"
  # Clean up an old dev preview if it exists. Real-user mode serves the UI from BACKEND_PORT.
  kill_port "$FRONTEND_PORT"

  start_app
  wait_for_url "backend" "http://${BACKEND_HOST}:${BACKEND_PORT}/health" 60 "${LOG_DIR}/app.log"
  wait_for_url "frontend" "http://${BACKEND_HOST}:${BACKEND_PORT}/" 30 "${LOG_DIR}/app.log"

  log "done"
  log "app:  http://${BACKEND_HOST}:${BACKEND_PORT}"
  log "logs:     ${LOG_DIR}"
}

main "$@"
