#!/bin/bash
# Unified V3 production control surface.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONTROL_SCRIPT="${SCRIPT_DIR}/control.sh"
cd "$PROJECT_ROOT"

export PYTHONPATH="${PYTHONPATH:-}:${PROJECT_ROOT}/src"
STATE_ROOT="${STATE_ROOT:-${HOME}/.100x_v3}"
LOG_DIR="${STATE_ROOT}/logs"
PID_DIR="${STATE_ROOT}/pids"
ROLE_DIR="${STATE_ROOT}/roles"
LAUNCHD_DIR="${HOME}/Library/LaunchAgents"
TMUX_SESSION="${TMUX_SESSION:-100x_v3_24h}"

mkdir -p "$LOG_DIR" "$PID_DIR" "$ROLE_DIR"

ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

resolve_python() {
  if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "$PROJECT_ROOT/.venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    command -v python3
  fi
}

PYTHON="$(resolve_python)"

role_label() {
  echo "com.100x.v3.$1"
}

role_pid_file() {
  echo "$PID_DIR/$1.pid"
}

role_log_file() {
  echo "$LOG_DIR/$1.log"
}

role_heartbeat_file() {
  echo "$ROLE_DIR/$1.json"
}

config_value() {
  "$PYTHON" - "$1" <<'PY'
import sys
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader

config = ConfigLoader(project_root=Path.cwd()).load()
value = config
for part in sys.argv[1].split("."):
    value = getattr(value, part)
print(value)
PY
}

write_heartbeat() {
  local role="$1"
  local status="${2:-running}"
  local detail="${3:-}"
  local heartbeat_pid="${CONTROL_ROLE_PID:-$$}"
  local child_pid="${CONTROL_CHILD_PID:-}"
  "$PYTHON" - "$role" "$status" "$detail" "$(role_heartbeat_file "$role")" "$heartbeat_pid" "$child_pid" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

role, status, detail, path, pid, child_pid = sys.argv[1:7]

def parse_pid(value):
    try:
        return int(value) if value else None
    except ValueError:
        return value or None

payload = {
    "role": role,
    "status": status,
    "detail": detail,
    "pid": parse_pid(pid),
    "child_pid": parse_pid(child_pid),
    "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
}
target = Path(path)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
}

role_command() {
  local role="$1"
  case "$role" in
    scheduler-loop)
      local interval
      interval="$(config_value scheduler.interval_seconds)"
      printf '%q ' "$PYTHON" -m knowledge_extractor_v3.scheduler --loop --interval "$interval"
      ;;
    worker-loop)
      local poll limit
      poll="$(config_value worker.poll_interval_seconds)"
      limit="$(config_value worker.batch_size)"
      printf '%q ' "$PYTHON" -m knowledge_extractor_v3.worker --loop --poll "$poll" --limit "$limit" --mode live
      ;;
    telegram-bot-loop)
      printf '%q ' "$PYTHON" -m knowledge_extractor_v3.telegram_bot --poll 30
      ;;
    health-monitor)
      printf '%q ' "$CONTROL_SCRIPT" health-monitor-loop
      ;;
    *)
      echo "Unknown role: $role" >&2
      return 1
      ;;
  esac
}

role_run() {
  local role="$1"
  local log_file pid_file command child exit_code restart_delay
  log_file="$(role_log_file "$role")"
  pid_file="$(role_pid_file "$role")"
  echo "$$" > "$pid_file"
  export CONTROL_ROLE_PID="$$"
  write_heartbeat "$role" "starting"
  restart_delay="${ROLE_RESTART_DELAY_SECONDS:-60}"

  if [ "$role" = "telegram-bot-loop" ] && [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    while true; do
      write_heartbeat "$role" "waiting" "TELEGRAM_BOT_TOKEN is not configured"
      sleep 300
    done
  fi

  command="$(role_command "$role")"
  trap 'write_heartbeat "'"$role"'" "stopping"; if [ -n "${child:-}" ]; then kill "$child" >/dev/null 2>&1 || true; wait "$child" >/dev/null 2>&1 || true; fi; exit 0' TERM INT

  while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting $role: $command" >> "$log_file"
    bash -lc "$command" >> "$log_file" 2>&1 &
    child=$!
    export CONTROL_CHILD_PID="$child"
    while kill -0 "$child" >/dev/null 2>&1; do
      write_heartbeat "$role" "running" "child_pid=$child"
      sleep 30
    done

    set +e
    wait "$child"
    exit_code=$?
    set -e

    write_heartbeat "$role" "exited" "exit_code=$exit_code"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $role exited with code $exit_code; restarting in ${restart_delay}s" >> "$log_file"
    write_heartbeat "$role" "restarting" "exit_code=$exit_code; delay=${restart_delay}s"
    sleep "$restart_delay"
  done
}

health_monitor_loop() {
  local log_file="$LOG_DIR/health-monitor.log"
  while true; do
    write_heartbeat "health-monitor" "running"
    "$PYTHON" -m knowledge_extractor_v3.health --json > "$STATE_ROOT/health.json" 2>> "$log_file" || true
    restart_stale_roles >> "$log_file" 2>&1 || true
    sleep 300
  done
}

restart_stale_roles() {
  local health_file="$STATE_ROOT/health.json"
  [ -f "$health_file" ] || return 0
  "$PYTHON" - "$health_file" <<'PY' | while read -r role; do
import json
import sys
from pathlib import Path

try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}

for check in data.get("checks", []):
    if check.get("name") != "role_locks":
        continue
    for role in check.get("detail", {}).get("stale_roles", []):
        if role != "health-monitor":
            print(role)
PY
    if [ -n "$role" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] health monitor starting stale role: $role"
      start_role "$role"
    fi
  done
}

start_role() {
  local role="$1"
  local pid_file log_file
  pid_file="$(role_pid_file "$role")"
  log_file="$(role_log_file "$role")"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "$role already running (pid $(cat "$pid_file"))"
    return
  fi
  nohup "$CONTROL_SCRIPT" role-run "$role" >> "$log_file" 2>&1 &
  echo "$!" > "$pid_file"
  echo "started $role (pid $!)"
}

stop_role() {
  local role="$1"
  local pid_file
  pid_file="$(role_pid_file "$role")"
  local plist="$LAUNCHD_DIR/$(role_label "$role").plist"
  if [ -f "$plist" ]; then
    launchctl unload "$plist" >/dev/null 2>&1 || true
  fi
  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" || true
    fi
    rm -f "$pid_file"
  fi
  write_heartbeat "$role" "stopped"
}

all_roles() {
  echo "scheduler-loop worker-loop telegram-bot-loop health-monitor"
}

start_all() {
  for role in $(all_roles); do
    start_role "$role"
  done
}

stop_all() {
  for role in $(all_roles); do
    stop_role "$role"
  done
}

require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is required for this command" >&2
    return 1
  fi
}

start_tmux() {
  require_tmux
  if tmux has-session -t "$TMUX_SESSION" >/dev/null 2>&1; then
    echo "tmux session already running: $TMUX_SESSION"
    return
  fi

  stop_all >/dev/null 2>&1 || true
  tmux new-session -d -s "$TMUX_SESSION" -n scheduler-loop -c "$PROJECT_ROOT" "$(printf '%q ' "$CONTROL_SCRIPT" role-run scheduler-loop)"
  tmux new-window -t "$TMUX_SESSION" -n worker-loop -c "$PROJECT_ROOT" "$(printf '%q ' "$CONTROL_SCRIPT" role-run worker-loop)"
  tmux new-window -t "$TMUX_SESSION" -n telegram-bot-loop -c "$PROJECT_ROOT" "$(printf '%q ' "$CONTROL_SCRIPT" role-run telegram-bot-loop)"
  tmux new-window -t "$TMUX_SESSION" -n health-monitor -c "$PROJECT_ROOT" "$(printf '%q ' "$CONTROL_SCRIPT" role-run health-monitor)"
  echo "started tmux session: $TMUX_SESSION"
}

restart_tmux() {
  require_tmux
  tmux kill-session -t "$TMUX_SESSION" >/dev/null 2>&1 || true
  stop_all >/dev/null 2>&1 || true
  start_tmux
}

tmux_status() {
  require_tmux
  if tmux has-session -t "$TMUX_SESSION" >/dev/null 2>&1; then
    tmux list-windows -t "$TMUX_SESSION"
  else
    echo "tmux session stopped: $TMUX_SESSION"
  fi
}

recover() {
  if command -v tmux >/dev/null 2>&1; then
    if ! start_tmux; then
      echo "tmux start failed; falling back to nohup roles" >&2
      start_all
    fi
  else
    start_all
  fi
  sleep 3
  status
}

status() {
  echo "V3 status"
  echo "Project: $PROJECT_ROOT"
  echo "State:   $STATE_ROOT"
  echo
  for role in $(all_roles); do
    local pid_file state
    pid_file="$(role_pid_file "$role")"
    state="stopped"
    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
      state="running pid=$(cat "$pid_file")"
    fi
    echo "$role: $state"
    if [ -f "$(role_heartbeat_file "$role")" ]; then
      "$PYTHON" - "$role" "$(role_heartbeat_file "$role")" <<'PY'
import json
import sys
from pathlib import Path
role, path = sys.argv[1:3]
try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
except Exception:
    data = {}
if data:
    print(f"  heartbeat: {data.get('status')} at {data.get('updated_at')} {data.get('detail', '')}".rstrip())
PY
    fi
  done
  echo
  "$CONTROL_SCRIPT" queue-status || true
}

queue_status() {
  "$PYTHON" <<'PY'
from pathlib import Path
from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.queue_store import QueueStore

loader = ConfigLoader(project_root=Path.cwd())
config = loader.load()
queue = QueueStore(loader.expand_path(config.runtime.queue_db_path))
counts = queue.count_by_status()
print("Queue:")
if counts:
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
else:
    print("  empty")
print(f"Sources loaded: {len(config.sources)}")
PY
}

doctor() {
  echo "[doctor] preflight"
  bash "$PROJECT_ROOT/scripts/run-v3.sh" preflight
  echo
  echo "[doctor] health"
  "$PYTHON" -m knowledge_extractor_v3.health || true
  echo
  echo "[doctor] multi-channel health"
  "$PYTHON" - <<'PY'
from knowledge_extractor_v3.fetchers.multi_channel import AgentReachFetcher
for name, status in AgentReachFetcher().health_check().items():
    print(f"  {name}: {status}")
PY
}

run_once() {
  bash "$PROJECT_ROOT/scripts/run-v3.sh" scheduler-once --limit "${1:-20}"
  bash "$PROJECT_ROOT/scripts/run-v3.sh" worker-once --limit "${2:-10}" --mode staging
  "$PYTHON" -m knowledge_extractor_v3.health || true
}

xml_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

print_plist() {
  local role="$1"
  local label stdout stderr script cwd
  label="$(role_label "$role")"
  stdout="$(role_log_file "$role" | xml_escape)"
  stderr="$(role_log_file "$role" | xml_escape)"
  script="$(printf '%s' "$CONTROL_SCRIPT" | xml_escape)"
  cwd="$(printf '%s' "$PROJECT_ROOT" | xml_escape)"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>WorkingDirectory</key><string>${cwd}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${script}</string>
    <string>role-run</string>
    <string>${role}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${stdout}</string>
  <key>StandardErrorPath</key><string>${stderr}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>${PROJECT_ROOT}/src</string>
    <key>STATE_ROOT</key><string>${STATE_ROOT}</string>
  </dict>
</dict>
</plist>
EOF
}

install_autostart() {
  mkdir -p "$LAUNCHD_DIR"
  for role in $(all_roles); do
    local plist="$LAUNCHD_DIR/$(role_label "$role").plist"
    print_plist "$role" > "$plist"
    launchctl unload "$plist" >/dev/null 2>&1 || true
    launchctl load "$plist"
    echo "installed $plist"
  done
}

uninstall_autostart() {
  for role in $(all_roles); do
    local plist="$LAUNCHD_DIR/$(role_label "$role").plist"
    launchctl unload "$plist" >/dev/null 2>&1 || true
    rm -f "$plist"
    echo "removed $plist"
  done
}

logs() {
  local role="${1:-worker-loop}"
  tail -f "$(role_log_file "$role")"
}

usage() {
  cat <<EOF
Usage: scripts/control.sh <command>

Commands:
  start | stop | restart | status | doctor | logs [role]
  recover | start-tmux | restart-tmux | tmux-status
  run-once [scheduler_limit] [worker_limit]
  install-autostart | uninstall-autostart
  print-plist <role>
EOF
}

command="${1:-help}"
shift || true

case "$command" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  recover) recover ;;
  start-tmux) start_tmux ;;
  restart-tmux) restart_tmux ;;
  tmux-status) tmux_status ;;
  status) status ;;
  doctor) doctor ;;
  logs) logs "${1:-worker-loop}" ;;
  run-once) run_once "${1:-20}" "${2:-10}" ;;
  install-autostart) install_autostart ;;
  uninstall-autostart) uninstall_autostart ;;
  print-plist) print_plist "${1:?role required}" ;;
  role-run) role_run "${1:?role required}" ;;
  health-monitor-loop) health_monitor_loop ;;
  queue-status) queue_status ;;
  help|--help|-h) usage ;;
  *) usage; exit 1 ;;
esac
